"""Research module (not in the decision path): LoRA adapters and the SFT dataset builder."""

import json

import pytest

from sentinel.finetune.dataset import build_records, split_and_write

torch = pytest.importorskip("torch")

HEADLINES = {  # small illustrative corpus used only to exercise the training loop (not a model of record)
    0: ["company charged with fraud", "executives indicted for bribery", "firm pleads guilty to money laundering"],
    1: ["company opens new factory", "firm reports quarterly growth", "company appoints new chief executive"],
}


def test_transformer_and_lora_mechanics():
    from sentinel.finetune.lora import LoRALinear, apply_lora, merge_all, trainable_fraction
    from sentinel.finetune.transformer import TrainConfig, TransformerClassifier, Vocab, accuracy, train_classifier

    texts = [t for lbl in HEADLINES for t in HEADLINES[lbl]] * 8
    labels = [lbl for lbl in HEADLINES for _ in HEADLINES[lbl]] * 8
    vocab = Vocab(texts)
    X = torch.tensor([vocab.encode(t, 12) for t in texts])
    y = torch.tensor(labels)
    torch.manual_seed(0)
    model = TransformerClassifier(len(vocab), 2, d_model=32, n_heads=4, max_len=12)
    losses = train_classifier(model, X, y, TrainConfig(epochs=8, max_len=12))
    assert losses[-1] < losses[0] and accuracy(model, X, y) > 0.9
    replaced = apply_lora(model, ("q_proj", "v_proj", "head_proj"), r=4)
    assert "blocks.0.attn.q_proj" in replaced and trainable_fraction(model) < 0.2
    before = model(X[:4]).detach()
    assert torch.allclose(before, model(X[:4]).detach())
    train_classifier(model, X, y, TrainConfig(epochs=1, max_len=12))
    adapted = model(X[:4]).detach()
    assert merge_all(model) == len(replaced) and torch.allclose(adapted, model(X[:4]).detach(), atol=1e-5)
    with pytest.raises(ValueError):
        LoRALinear(torch.nn.Linear(2, 2), r=0)
    with pytest.raises(ValueError):
        TransformerClassifier(10, 2, d_model=30, n_heads=4)


def test_sft_dataset_only_human_approved_and_redacted(tmp_path):
    cases = [
        {"entity": "A", "review_status": "approved", "findings": ["contact a@b.com"], "report": "Memo [d#0] a@b.com"},
        {"entity": "B", "review_status": "rejected", "report": "x"},
        {"entity": "D", "review_status": "approved", "findings": [], "report": "Memo D [d#1]"},
    ]
    chat = build_records(cases)
    assert len(chat) == 2 and "a@b.com" not in json.dumps(chat)
    assert set(build_records(cases, fmt="instruction")[0]) == {"instruction", "input", "output"}
    assert split_and_write(chat, tmp_path, val_frac=0.5) == {"val": 1, "train": 1}
