"""Research module (not in the decision path): transformer + LoRA mechanics (requires torch)."""

import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

ROOT = Path(__file__).resolve().parents[2]


def _real_titles() -> tuple[list[str], list[int]]:
    """Hand-labelled real DOJ press-release titles (data/eval/media_roles.json): 1 = entity is the subject."""
    items = json.loads((ROOT / "data" / "eval" / "media_roles.json").read_text())["items"]
    return [i["title"].lower() for i in items], [int(i["role"] == "subject") for i in items]


def test_transformer_and_lora_mechanics():
    from sentinel.finetune.lora import LoRALinear, apply_lora, merge_all, trainable_fraction
    from sentinel.finetune.transformer import TrainConfig, TransformerClassifier, Vocab, accuracy, train_classifier

    titles, roles = _real_titles()
    texts, labels = titles * 8, roles * 8
    vocab = Vocab(texts)
    X = torch.tensor([vocab.encode(t, 24) for t in texts])
    y = torch.tensor(labels)
    torch.manual_seed(0)
    model = TransformerClassifier(len(vocab), 2, d_model=32, n_heads=4, max_len=24)
    # mechanics check (the loop can fit a small real corpus), not a model of record
    losses = train_classifier(model, X, y, TrainConfig(epochs=12, max_len=24))
    assert losses[-1] < losses[0] and accuracy(model, X, y) > 0.9
    replaced = apply_lora(model, ("q_proj", "v_proj", "head_proj"), r=4)
    assert "blocks.0.attn.q_proj" in replaced and trainable_fraction(model) < 0.2
    before = model(X[:4]).detach()
    assert torch.allclose(before, model(X[:4]).detach())
    train_classifier(model, X, y, TrainConfig(epochs=1, max_len=24))
    adapted = model(X[:4]).detach()
    assert merge_all(model) == len(replaced) and torch.allclose(adapted, model(X[:4]).detach(), atol=1e-5)
    with pytest.raises(ValueError):
        LoRALinear(torch.nn.Linear(2, 2), r=0)
    with pytest.raises(ValueError):
        TransformerClassifier(10, 2, d_model=30, n_heads=4)
