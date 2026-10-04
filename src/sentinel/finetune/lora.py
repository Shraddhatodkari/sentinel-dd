"""LoRA (Low-Rank Adaptation, Hu et al. 2021) implemented from first principles in PyTorch.

``W' = W + (alpha / r) * B @ A`` with ``A ∈ R^{r×in}``, ``B ∈ R^{out×r}``, B initialised to zero so the
adapted model starts identical to the base model. Only A and B are trained (typically <1% of params),
and adapters can be merged back into W for zero-latency inference.
"""

from __future__ import annotations

import math

import torch
from torch import nn


class LoRALinear(nn.Module):
    def __init__(self, base: nn.Linear, r: int = 8, alpha: int = 16, dropout: float = 0.0) -> None:
        super().__init__()
        if r <= 0:
            raise ValueError("rank must be positive")
        self.base = base
        self.r = r
        self.scaling = alpha / r
        self.lora_A = nn.Parameter(torch.empty(r, base.in_features))
        self.lora_B = nn.Parameter(torch.zeros(base.out_features, r))
        nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))
        self.dropout = nn.Dropout(dropout) if dropout > 0 else nn.Identity()
        self.merged = False
        for p in self.base.parameters():
            p.requires_grad = False

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.base(x)
        if not self.merged:
            out = out + (self.dropout(x) @ self.lora_A.T @ self.lora_B.T) * self.scaling
        return out

    @torch.no_grad()
    def merge(self) -> None:
        if not self.merged:
            self.base.weight += (self.lora_B @ self.lora_A) * self.scaling
            self.merged = True


def apply_lora(
    model: nn.Module,
    target_substrings: tuple[str, ...] = ("linear", "proj", "fc"),
    r: int = 8,
    alpha: int = 16,
    dropout: float = 0.0,
) -> list[str]:
    """Replace matching nn.Linear sub-modules with LoRA wrappers; freeze everything else."""
    for p in model.parameters():
        p.requires_grad = False
    replaced: list[str] = []
    for name, module in list(model.named_modules()):
        for child_name, child in list(module.named_children()):
            full = f"{name}.{child_name}" if name else child_name
            if isinstance(child, nn.Linear) and any(t in child_name for t in target_substrings):
                setattr(module, child_name, LoRALinear(child, r, alpha, dropout))
                replaced.append(full)
    return replaced


def trainable_fraction(model: nn.Module) -> float:
    total = sum(p.numel() for p in model.parameters())
    train = sum(p.numel() for p in model.parameters() if p.requires_grad)
    return train / max(total, 1)


def merge_all(model: nn.Module) -> int:
    n = 0
    for m in model.modules():
        if isinstance(m, LoRALinear):
            m.merge()
            n += 1
    return n
