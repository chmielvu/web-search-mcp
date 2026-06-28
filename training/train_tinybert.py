#!/usr/bin/env python3
"""Fine-tune TinyBERT-4L for 6-class search intent classification."""

import json
from pathlib import Path

import numpy as np
from datasets import Dataset
from sklearn.metrics import accuracy_score, f1_score, classification_report
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    Trainer,
    TrainingArguments,
    DataCollatorWithPadding,
)

MODEL_ID = "huawei-noah/TinyBERT_General_4L_312D"
NUM_LABELS = 6
MAX_LENGTH = 64
DATA_DIR = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "tinybert-intent-classifier"

from collections import Counter

LABEL2ID = {
    "general": 0,
    "ai_coding_and_infrastructure": 1,
    "digital_humanities": 2,
    "comparison": 3,
    "social_media": 4,
    "news": 5,
}
ID2LABEL = {v: k for k, v in LABEL2ID.items()}

print(f"Loading tokenizer + model from {MODEL_ID}...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)

# Compute class weights from training data to handle imbalance
train_labels_raw = [json.loads(l)["label"] for l in open(DATA_DIR / "train.jsonl") if l.strip()]
label_counts = Counter(train_labels_raw)
total = len(train_labels_raw)
# Weight = total / (n_classes * count) — inverse frequency
class_weights = [
    total / (NUM_LABELS * label_counts[ID2LABEL[i]])
    for i in range(NUM_LABELS)
]
print(f"Class weights: {class_weights}")

model = AutoModelForSequenceClassification.from_pretrained(
    MODEL_ID,
    num_labels=NUM_LABELS,
    ignore_mismatched_sizes=True,
)
model.config.label2id = LABEL2ID
model.config.id2label = ID2LABEL


def tokenize(examples):
    return tokenizer(
        examples["text"],
        padding="max_length",
        truncation=True,
        max_length=MAX_LENGTH,
    )


def encode_labels(examples):
    examples["label"] = LABEL2ID[examples["label"]]
    return examples


print("Loading datasets...")
train_ds = (
    Dataset.from_json(str(DATA_DIR / "train.jsonl"))
    .map(encode_labels)
    .map(tokenize, batched=True)
)
val_ds = (
    Dataset.from_json(str(DATA_DIR / "val.jsonl"))
    .map(encode_labels)
    .map(tokenize, batched=True)
)
print(f"  Train: {len(train_ds)} samples")
print(f"  Val:   {len(val_ds)} samples")

data_collator = DataCollatorWithPadding(tokenizer=tokenizer)


def compute_metrics(eval_pred):
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=-1)
    return {
        "accuracy": accuracy_score(labels, preds),
        "f1_macro": f1_score(labels, preds, average="macro"),
    }


import torch

class WeightedTrainer(Trainer):
    def __init__(self, class_weights_tensor, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.class_weights = class_weights_tensor

    def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        logits = outputs.logits
        loss_fct = torch.nn.CrossEntropyLoss(weight=self.class_weights)
        loss = loss_fct(logits.view(-1, model.config.num_labels), labels.view(-1))
        return (loss, outputs) if return_outputs else loss


training_args = TrainingArguments(
    output_dir=str(OUTPUT_DIR),
    num_train_epochs=15,
    per_device_train_batch_size=16,
    per_device_eval_batch_size=32,
    learning_rate=5e-4,
    weight_decay=0.01,
    warmup_ratio=0.1,
    eval_strategy="epoch",
    save_strategy="epoch",
    load_best_model_at_end=True,
    metric_for_best_model="f1_macro",
    fp16=False,  # CPU training
    report_to="none",
    logging_steps=10,
)

class_weights_tensor = torch.tensor(class_weights, dtype=torch.float32)

trainer = WeightedTrainer(
    model=model,
    args=training_args,
    train_dataset=train_ds,
    eval_dataset=val_ds,
    data_collator=data_collator,
    compute_metrics=compute_metrics,
    class_weights_tensor=class_weights_tensor,
)

print("\n=== Starting training ===")
trainer.train()

print("\n=== Saving model ===")
trainer.save_model(str(OUTPUT_DIR))
tokenizer.save_pretrained(str(OUTPUT_DIR))

# Final evaluation with classification report
print("\n=== Final Evaluation ===")
predictions = trainer.predict(val_ds)
preds = np.argmax(predictions.predictions, axis=-1)
print(classification_report(
    predictions.label_ids, preds,
    target_names=[ID2LABEL[i] for i in range(NUM_LABELS)],
))