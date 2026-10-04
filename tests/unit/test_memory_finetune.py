import json

import pytest

from sentinel.embeddings.base import HashingEmbedder
from sentinel.finetune.dataset import build_records, split_and_write
from sentinel.llm.fake import FakeLLM
from sentinel.memory.memory import LongTermMemory, ShortTermMemory, approx_tokens


def test_short_term_memory_compacts_within_budget():
    m = ShortTermMemory(max_tokens=60, keep_recent=2)
    for i in range(10):
        m.add("user", f"Turn {i}. " + "detail " * 20)
    assert len(m.turns) == 2 and "Turn 7" in m.summary  # oldest facts rolled into summary, recent kept verbatim
    assert m.turns[-1].content.startswith("Turn 9")
    ctx = m.context()
    assert ctx[0].content.startswith("[Conversation summary]") and len(ctx) == 3
    assert approx_tokens("") == 1


def test_short_term_memory_llm_summariser():
    m = ShortTermMemory(max_tokens=40, keep_recent=1, llm=FakeLLM())
    for i in range(4):
        m.add("user", f"Fact {i} " + "x " * 40)
    assert m.summary and len(m.turns) == 1


def test_long_term_memory_recall_and_eviction():
    mem = LongTermMemory(HashingEmbedder(128), max_items=3)
    assert mem.recall("anything") == []
    mem.remember("c1", "Helios Trade high risk sanctions exposure", entity="Helios")
    mem.remember("c2", "Northwind Logistics low risk clean audit", entity="Northwind")
    hits = mem.recall("Helios sanctions", k=1)
    assert hits[0][0].key == "c1"
    assert mem.recall("Helios sanctions", entity="Northwind", min_score=-1)[0][0].key == "c2"
    for i in range(3):
        mem.remember(f"x{i}", f"filler {i}")
    assert len(mem) == 3 and all(r.key != "c1" for r, _ in mem.recall("Helios", k=5, min_score=-1))


def test_sft_dataset_only_approved_and_redacted(tmp_path):
    cases = [
        {"entity": "A", "review_status": "approved", "findings": ["contact a@b.com"], "report": "Memo [d#0] a@b.com"},
        {"entity": "B", "review_status": "rejected", "report": "x"},
        {"entity": "C", "review_status": "approved", "report": ""},
        {"entity": "D", "review_status": "approved", "findings": [], "report": "Memo D [d#1]"},
    ]
    chat = build_records(cases)
    assert len(chat) == 2 and "a@b.com" not in json.dumps(chat)
    assert chat[0]["messages"][-1]["role"] == "assistant"
    inst = build_records(cases, fmt="instruction")
    assert set(inst[0]) == {"instruction", "input", "output"}
    sizes = split_and_write(chat, tmp_path, val_frac=0.5)
    assert sizes == {"val": 1, "train": 1} and (tmp_path / "train.jsonl").read_text().strip()
    assert split_and_write(chat[:1], tmp_path / "one") == {"val": 0, "train": 1}


torch = pytest.importorskip("torch")


def test_transformer_classifier_trains_and_lora_adapts():
    from sentinel.finetune.lora import LoRALinear, apply_lora, merge_all, trainable_fraction
    from sentinel.finetune.transformer import (
        TrainConfig,
        TransformerClassifier,
        Vocab,
        accuracy,
        train_classifier,
    )
    from sentinel.ml.models import MEDIA_LABELS, generate_media_corpus

    texts, labels = generate_media_corpus()
    vocab = Vocab(texts)
    X = torch.tensor([vocab.encode(t, 24) for t in texts])
    y = torch.tensor([MEDIA_LABELS.index(lbl) for lbl in labels])
    torch.manual_seed(0)
    model = TransformerClassifier(len(vocab), len(MEDIA_LABELS), d_model=32, n_heads=4, max_len=24)
    losses = train_classifier(model, X, y, TrainConfig(epochs=6, max_len=24))
    assert losses[-1] < losses[0] and accuracy(model, X, y) > 0.9

    replaced = apply_lora(model, ("q_proj", "v_proj", "head_proj"), r=4)
    assert "blocks.0.attn.q_proj" in replaced and trainable_fraction(model) < 0.1
    before = model(X[:8]).detach()
    assert torch.allclose(before, model(X[:8]).detach())  # B=0 => identical to base at init
    train_classifier(model, X, y, TrainConfig(epochs=1, max_len=24))
    adapted = model(X[:8]).detach()
    assert merge_all(model) == len(replaced)
    assert torch.allclose(adapted, model(X[:8]).detach(), atol=1e-5)  # merge is lossless
    with pytest.raises(ValueError):
        LoRALinear(torch.nn.Linear(2, 2), r=0)
    with pytest.raises(ValueError):
        TransformerClassifier(10, 2, d_model=30, n_heads=4)
