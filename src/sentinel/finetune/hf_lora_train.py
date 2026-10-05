"""QLoRA supervised fine-tuning of an open-weights LLM (e.g. Llama-3.1-8B-Instruct, Mistral-7B)
on the memo-writing dataset produced by :mod:`sentinel.finetune.dataset`.

Run on a GPU node (or Vertex AI Custom Job / SageMaker Training Job)::

    python -m sentinel.finetune.hf_lora_train --base meta-llama/Llama-3.1-8B-Instruct --revision <commit-sha> \
        --data var/sft --out var/adapters/memo-v1

Not executed in CI (requires GPU + model weights); covered by the dataset & LoRA unit tests.
"""

from __future__ import annotations

import argparse
import os


def _mlflow_uri() -> str | None:
    uri = os.environ.get("MLFLOW_TRACKING_URI") or os.environ.get("SENTINEL_MLFLOW_TRACKING_URI")
    if uri:
        os.environ["MLFLOW_TRACKING_URI"] = uri  # the transformers MLflow callback reads this variable
    return uri


def main(argv: list[str] | None = None) -> None:  # pragma: no cover
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--revision", required=True, help="pinned model commit SHA (supply-chain safety)")
    args = ap.parse_args(argv)

    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        DataCollatorForLanguageModeling,
        Trainer,
        TrainingArguments,
    )

    tok = AutoTokenizer.from_pretrained(args.base, revision=args.revision)
    tok.pad_token = tok.pad_token or tok.eos_token
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.base, revision=args.revision, quantization_config=bnb, device_map="auto"
    )
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(
        model,
        LoraConfig(
            r=args.rank,
            lora_alpha=args.rank * 2,
            lora_dropout=0.05,
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        ),
    )
    model.print_trainable_parameters()

    ds = load_dataset(  # nosec B615 - local JSONL files, not a Hub download
        "json", data_files={"train": f"{args.data}/train.jsonl", "val": f"{args.data}/val.jsonl"}
    )

    def fmt(ex):
        text = tok.apply_chat_template(ex["messages"], tokenize=False)
        return tok(text, truncation=True, max_length=2048)

    ds = ds.map(fmt, remove_columns=ds["train"].column_names)
    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            args.out,
            num_train_epochs=args.epochs,
            per_device_train_batch_size=2,
            gradient_accumulation_steps=8,
            learning_rate=2e-4,
            bf16=True,
            eval_strategy="epoch",
            save_strategy="epoch",
            logging_steps=10,
            report_to=["mlflow"] if _mlflow_uri() else [],
            lr_scheduler_type="cosine",
            warmup_ratio=0.03,
        ),
        train_dataset=ds["train"],
        eval_dataset=ds["val"],
        data_collator=DataCollatorForLanguageModeling(tok, mlm=False),
    )
    trainer.train()
    model.save_pretrained(args.out)


if __name__ == "__main__":  # pragma: no cover
    main()
