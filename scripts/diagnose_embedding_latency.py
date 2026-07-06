"""Live diagnostic for HF embedding latency under contention.

This script measures how long HF embedding calls take in isolation vs. when they
are launched concurrently (as the pipeline does). After removing the global
`_HF_REQUEST_GATE`, there should be no artificial serialization between concurrent
callers.

Run from repo root with the project venv:
    .\\.venv\\Scripts\\python.exe scripts\\diagnose_embedding_latency.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# Make repo source importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.embeddings import embed_query, embed_texts
from kindly_web_search_mcp_server.embeddings import hf_inference


async def _heavy_contention_call(query: str, candidate_texts: list[str]) -> float:
    """Simulate the real pipeline: early query, Qdrant, bi-encoder, index write."""
    start = time.monotonic()
    tasks = []
    tasks.append(asyncio.create_task(embed_query(query, timeout=15.0)))
    await asyncio.sleep(0.1)
    tasks.append(asyncio.create_task(embed_query(query, timeout=15.0)))  # Qdrant
    await asyncio.sleep(0.5)
    tasks.append(
        asyncio.create_task(
            embed_texts(candidate_texts, timeout=15.0, max_retries=0)
        )
    )  # bi-encoder
    await asyncio.sleep(0.2)
    tasks.append(
        asyncio.create_task(
            embed_texts(candidate_texts[:10], timeout=15.0, max_retries=0)
        )
    )  # index write
    await asyncio.gather(*tasks)
    return time.monotonic() - start


async def main() -> None:
    # Representative workload: 28 bounded candidate texts
    query = "What are the latest advances in retrieval-augmented generation?"
    candidate_texts = [
        f"Candidate result {i}\nSnippet text about retrieval augmented generation and related topics."
        for i in range(28)
    ]

    print("=" * 60)
    print("HF Embedding Latency Diagnostic (post-gate removal)")
    print("=" * 60)

    gate_present = hasattr(hf_inference, "_HF_REQUEST_GATE")
    print(f"Global _HF_REQUEST_GATE present: {gate_present}")
    if gate_present:
        print(
            "WARNING: the global gate is still present; concurrent callers will be serialized."
        )

    # 1. Isolated call
    print("\n1. Isolated embed_texts (28 texts, one batch)")
    start = time.monotonic()
    await embed_texts(candidate_texts, timeout=15.0, max_retries=0)
    print(f"  Total elapsed: {time.monotonic() - start:.3f}s")

    # 2. Contended call: four concurrent embedding callers
    print("\n2. Heavy contention (4 concurrent callers)")
    total = await _heavy_contention_call(query, candidate_texts)
    print(f"  Total elapsed (4 concurrent callers): {total:.3f}s")

    print("\n" + "=" * 60)
    print("Diagnostic complete.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
