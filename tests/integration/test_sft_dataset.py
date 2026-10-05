"""SFT dataset builder on real (replayed) cases: only human-approved memos become examples, DLP-masked."""

import json

from sentinel.finetune.dataset import approved_cases, build_records, findings_prompt, split_and_write


def test_only_human_approved_cases_become_examples(service, principal, container, tmp_path):
    alice, bob, sara, carol = (principal(u) for u in ("alice", "bob", "sara", "carol"))
    apple = service.create(alice, "Apple Inc.")
    sber = service.create(alice, "Sberbank")
    service.decide(bob, apple["case_id"], True, "Evidence reviewed; no adverse findings on the subject.")
    service.decide(sara, sber["case_id"], False, "Subject is a sanctions potential match on OFAC and EU lists.")

    cases = approved_cases(service, carol)
    assert [c["case_id"] for c in cases] == [apple["case_id"]]
    prompt = findings_prompt(cases[0])
    assert prompt.startswith("Entity: Apple Inc.") and "POLICY tprm-policy" in prompt and "approved" not in prompt

    chat = build_records(cases, container.dlp)
    assert len(chat) == 1 and [m["role"] for m in chat[0]["messages"]] == ["system", "user", "assistant"]
    assert chat[0]["messages"][2]["content"] == container.dlp.redact(cases[0]["report"], "mask").text
    leaked = container.dlp.scan(json.dumps(chat))
    assert not [f for f in leaked if f.severity in ("high", "critical")]

    instr = build_records(cases, container.dlp, fmt="instruction")
    assert set(instr[0]) == {"instruction", "input", "output"}
    assert build_records([service.get(carol, sber["case_id"])]) == []  # rejected → excluded
    assert split_and_write(chat * 4, tmp_path, val_frac=0.25) == {"val": 1, "train": 3}
    assert len((tmp_path / "train.jsonl").read_text().splitlines()) == 3


def test_sft_export_cli(workdir, monkeypatch, capsys, service, principal):
    from sentinel import cli
    from sentinel.eval.harness import FIXTURES, offline_container

    alice, bob = principal("alice"), principal("bob")
    case = service.create(alice, "Apple Inc.")
    service.decide(bob, case["case_id"], True, "Evidence reviewed; no adverse findings on the subject.")
    monkeypatch.setattr(cli, "_container", lambda: offline_container(workdir, FIXTURES))
    assert cli.main(["ml", "sft-export", "--as", "carol", "--out", str(workdir / "sft")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["records"] == 1 and (workdir / "sft" / "train.jsonl").exists()
    assert cli.main(["ml", "sft-export", "--as", "nobody"]) == 2
