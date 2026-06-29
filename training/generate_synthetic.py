#!/usr/bin/env python3
"""Generate synthetic search queries for intent classification using distilabel + Gemini API.

Uses distilabel's GenerateTextClassificationData step with a custom GeminiLLM
class wrapping google-genai SDK (since litellm is incompatible with this API key).

Library choice: distilabel (argilla-io/distilabel v1.5.3) — purpose-built for
synthetic text classification data generation. Provides:
  - GenerateTextClassificationData step with difficulty/clarity knobs
  - Built-in JSON output format (input_text, label, misleading_label)
  - Pipeline orchestration with caching
  - Quality via difficulty levels (high school → PhD) and clarity levels
"""

from __future__ import annotations

import os
import json
import warnings
from pathlib import Path
from collections import Counter

warnings.filterwarnings("ignore")

# The user-provided key for Gemini API — set via env var GEMINI_API_KEY
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash-lite"

# Clear conflicting env vars so google-genai uses our key
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)

from google import genai
from google.genai import types as genai_types
from distilabel.models import AsyncLLM
from distilabel.models.llms.utils import prepare_output
from distilabel.pipeline import Pipeline
from distilabel.steps import LoadDataFromDicts
from distilabel.steps.tasks import GenerateTextClassificationData


class GeminiLLM(AsyncLLM):
    """Custom distilabel LLM class wrapping google-genai SDK."""

    api_key: str = ""
    model_id: str = ""

    def load(self) -> None:
        self._client = genai.Client(api_key=self.api_key)
        import logging
        logging.info(f"Loaded Gemini client for model: {self.model_id}")

    @property
    def model_name(self) -> str:
        return self.model_id

    async def agenerate(
        self,
        input: list[dict] | str,
        num_generations: int = 1,
        temperature: float = 0.8,
        max_tokens: int = 512,
        **kwargs: object,
    ) -> dict:
        """Generate text using google-genai async client.

        distilabel passes input as list of chat messages [{"role": "...", "content": "..."}].
        """
        # Convert chat messages to a single prompt string for Gemini
        if isinstance(input, list):
            prompt_parts = []
            for msg in input:
                content = msg.get("content", "")
                prompt_parts.append(content)
            prompt = "\n\n".join(prompt_parts)
        elif isinstance(input, str):
            prompt = input
        else:
            prompt = str(input)

        config = genai_types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        # Generate N responses
        generations = []
        for _ in range(num_generations):
            resp = await self._client.aio.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config=config,
            )
            generations.append(resp.text)

        return prepare_output(generations)


# Classification task definitions — each generates queries for one target class
# The "task" string tells GenerateTextClassificationData what the classification
# problem looks like, with the target class emphasized in the description.
TASKS = [
    {
        "task": "Classify the search query as one of: digital_humanities, general, ai_coding_and_infrastructure, comparison, social_media, news. digital_humanities means: digital preservation of ancient manuscripts, computational analysis of historical literature, text mining historical archives, GIS for archaeology, corpus linguistics, digital scholarly editions, TEI encoding, distant reading, stylometry, cultural heritage digitization.",
        "num_generations": 6,  # ×9 difficulty/clarity combos = 54 per task
    },
    {
        "task": "Classify the search query as one of: social_media, general, ai_coding_and_infrastructure, digital_humanities, comparison, news. social_media means: TikTok marketing trends, Instagram engagement strategies, YouTube influencer campaigns, Twitter/X trending analysis, Reddit community management, LinkedIn B2B content, social commerce, viral content patterns, social analytics, cross-platform scheduling.",
        "num_generations": 6,
    },
    {
        "task": "Classify the search query as one of: news, general, ai_coding_and_infrastructure, digital_humanities, comparison, social_media. news means: breaking news API integration, real-time event detection, news aggregation systems, media bias detection, automated journalism, fact-checking tools, news recommendation engines, fake news detection, current events tracking, news article summarization.",
        "num_generations": 6,
    },
    {
        "task": "Classify the search query as one of: comparison, general, ai_coding_and_infrastructure, digital_humanities, social_media, news. comparison means: explicit comparison of technologies, frameworks, products, or approaches. Queries like X vs Y, best tool for, alternatives to, which is better, benchmark comparison, feature comparison.",
        "num_generations": 6,
    },
]

DIFFICULTIES = ["high school", "college", "PhD"]
CLARITIES = ["clear", "understandable with some effort", "ambiguous"]


def build_pipeline() -> Pipeline:
    """Build the distilabel pipeline for synthetic data generation."""
    llm = GeminiLLM(api_key=GEMINI_API_KEY, model_id=GEMINI_MODEL)

    # Create one input row per (task, difficulty, clarity) combination
    task_inputs = []
    for task_def in TASKS:
        for difficulty in DIFFICULTIES:
            for clarity in CLARITIES:
                task_inputs.append({
                    "task": task_def["task"],
                    "difficulty": difficulty,
                    "clarity": clarity,
                })

    # Use max num_generations across tasks (distilabel applies one value per step)
    max_gens = max(t["num_generations"] for t in TASKS)

    with Pipeline(name="intent-synthetic-data") as pipeline:
        load = LoadDataFromDicts(data=task_inputs, batch_size=len(task_inputs))
        gen = GenerateTextClassificationData(
            llm=llm,
            language="English",
            num_generations=max_gens,
            input_batch_size=8,
        )
        load.connect(gen)

    return pipeline


def run():
    pipeline = build_pipeline()
    print(f"Pipeline: {len(TASKS)} classes × {len(DIFFICULTIES)} difficulties × {len(CLARITIES)} clarities")
    print(f"Model: {GEMINI_MODEL} via google-genai SDK")
    print(f"Expected output: ~{len(TASKS) * len(DIFFICULTIES) * len(CLARITIES) * 6} samples")
    print()

    distiset = pipeline.run(
        use_cache=False,
        parameters={
            "generate_text_classification_data_0": {
                "llm": {
                    "generation_kwargs": {
                        "kwargs": {},
                    },
                },
            },
        },
    )

    # Navigate distiset: distiset["default"]["train"] → Dataset
    ds = distiset["default"]["train"]
    print(f"\nGenerated {len(ds)} raw samples")

    # Extract input_text + label
    records = []
    for row in ds:
        text = row.get("input_text", "").strip()
        label = row.get("label", "").strip()
        if text and label:
            records.append({"text": text, "label": label})

    # Dedup
    seen = set()
    unique = []
    for rec in records:
        key = (rec["text"].casefold(), rec["label"].casefold())
        if key not in seen:
            seen.add(key)
            unique.append(rec)

    # Write
    output_path = Path("training/data/synthetic.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for rec in unique:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Raw: {len(records)} → Deduped: {len(unique)} → {output_path}")
    print("Distribution:")
    for k, v in Counter(r["label"] for r in unique).most_common():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    run()
