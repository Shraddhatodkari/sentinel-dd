"""A compact Transformer-encoder text classifier (PyTorch) for adverse-media triage.

Architecture (implemented from scratch): token + learned positional embeddings -> N x pre-LN blocks
[multi-head scaled dot-product self-attention -> GELU FFN] -> masked mean pooling -> classification head.
Small enough to train on CPU in seconds; the same training loop fine-tunes it with LoRA adapters
(see :mod:`sentinel.finetune.lora`).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import torch
from torch import nn

_TOK = re.compile(r"[a-z0-9]+")
PAD, UNK = 0, 1


class Vocab:
    def __init__(self, texts: list[str], min_freq: int = 1, max_size: int = 20_000) -> None:
        counts = Counter(t for s in texts for t in _TOK.findall(s.lower()))
        words = [w for w, c in counts.most_common(max_size) if c >= min_freq]
        self.itos = ["<pad>", "<unk>", *words]
        self.stoi = {w: i for i, w in enumerate(self.itos)}

    def encode(self, text: str, max_len: int) -> list[int]:
        ids = [self.stoi.get(t, UNK) for t in _TOK.findall(text.lower())][:max_len]
        return ids + [PAD] * (max_len - len(ids))

    def __len__(self) -> int:
        return len(self.itos)


class MultiHeadSelfAttention(nn.Module):
    """Scaled dot-product multi-head attention with explicit q/k/v/o projections
    (same naming as Llama/Mistral, so LoRA targets ``q_proj``/``v_proj`` transfer 1:1)."""

    def __init__(self, d_model: int, n_heads: int, dropout: float = 0.1) -> None:
        super().__init__()
        if d_model % n_heads:
            raise ValueError("d_model must be divisible by n_heads")
        self.h, self.dk = n_heads, d_model // n_heads
        self.q_proj = nn.Linear(d_model, d_model)
        self.k_proj = nn.Linear(d_model, d_model)
        self.v_proj = nn.Linear(d_model, d_model)
        self.o_proj = nn.Linear(d_model, d_model)
        self.drop = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, pad_mask: torch.Tensor) -> torch.Tensor:
        b, t, d = x.shape

        def split(z: torch.Tensor) -> torch.Tensor:
            return z.view(b, t, self.h, self.dk).transpose(1, 2)

        q, k, v = split(self.q_proj(x)), split(self.k_proj(x)), split(self.v_proj(x))
        scores = (q @ k.transpose(-2, -1)) / (self.dk**0.5)
        scores = scores.masked_fill(pad_mask[:, None, None, :], float("-inf"))
        attn = self.drop(torch.softmax(scores, dim=-1).nan_to_num(0.0))
        return self.o_proj((attn @ v).transpose(1, 2).reshape(b, t, d))


class EncoderBlock(nn.Module):
    def __init__(self, d_model: int, n_heads: int, dropout: float) -> None:
        super().__init__()
        self.ln1 = nn.LayerNorm(d_model)
        self.attn = MultiHeadSelfAttention(d_model, n_heads, dropout)
        self.ln2 = nn.LayerNorm(d_model)
        self.fc1 = nn.Linear(d_model, 4 * d_model)
        self.fc2 = nn.Linear(4 * d_model, d_model)
        self.drop = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, pad_mask: torch.Tensor) -> torch.Tensor:
        x = x + self.drop(self.attn(self.ln1(x), pad_mask))  # pre-LN residual
        return x + self.drop(self.fc2(torch.nn.functional.gelu(self.fc1(self.ln2(x)))))


class TransformerClassifier(nn.Module):
    def __init__(
        self,
        vocab_size: int,
        n_classes: int,
        d_model: int = 64,
        n_heads: int = 4,
        n_layers: int = 2,
        max_len: int = 32,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.tok = nn.Embedding(vocab_size, d_model, padding_idx=PAD)
        self.pos = nn.Embedding(max_len, d_model)
        self.blocks = nn.ModuleList(EncoderBlock(d_model, n_heads, dropout) for _ in range(n_layers))
        self.norm = nn.LayerNorm(d_model)
        self.head_proj = nn.Linear(d_model, n_classes)

    def forward(self, ids: torch.Tensor) -> torch.Tensor:
        mask = ids == PAD
        pos = torch.arange(ids.size(1), device=ids.device).unsqueeze(0)
        h = self.tok(ids) + self.pos(pos)
        for blk in self.blocks:
            h = blk(h, mask)
        h = self.norm(h)
        keep = (~mask).unsqueeze(-1).float()
        pooled = (h * keep).sum(1) / keep.sum(1).clamp(min=1.0)
        return self.head_proj(pooled)


@dataclass
class TrainConfig:
    epochs: int = 15
    lr: float = 3e-3
    batch_size: int = 32
    max_len: int = 32
    seed: int = 7


def train_classifier(model: nn.Module, X: torch.Tensor, y: torch.Tensor, cfg: TrainConfig) -> list[float]:
    torch.manual_seed(cfg.seed)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=cfg.lr)
    loss_fn = nn.CrossEntropyLoss()
    losses = []
    model.train()
    for _ in range(cfg.epochs):
        perm = torch.randperm(len(X))
        total = 0.0
        for i in range(0, len(X), cfg.batch_size):
            idx = perm[i : i + cfg.batch_size]
            opt.zero_grad()
            loss = loss_fn(model(X[idx]), y[idx])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += float(loss.detach()) * len(idx)
        losses.append(total / len(X))
    model.eval()
    return losses


@torch.no_grad()
def accuracy(model: nn.Module, X: torch.Tensor, y: torch.Tensor) -> float:
    model.eval()
    return float((model(X).argmax(-1) == y).float().mean())
