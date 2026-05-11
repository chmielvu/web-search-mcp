"""Rate-limited batch embeddings with concurrent request management."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .hf_inference import embed_texts as _embed_texts

logger = logging.getLogger(__name__)


class BatchLimitedEmbeddings:
    """
    Batch embeddings with rate limiting and concurrent request control.

    Features:
    - Maximum batch size: 32 texts per request
    - Minimum delay: 0.5s between batches
    - Semaphore: 3 concurrent requests maximum
    - Raises provider/dimension errors instead of fabricating vectors

    Example:
        >>> embedder = BatchLimitedEmbeddings()
        >>> # Single query
        >>> emb = await embedder.embed_query("search")
        >>> # Multiple texts (split into batches if needed)
        >>> embeddings = await embedder.embed_texts([...] * 100)
        >>> # With custom client
        >>> async with httpx.AsyncClient(timeout=30) as client:
        ...     emb = await embedder.embed_query("search", http_client=client)
    """

    def __init__(
        self,
        *,
        max_batch_size: int = 32,
        min_delay_seconds: float = 0.5,
        max_concurrent: int = 3,
        timeout: float = 30.0,
    ) -> None:
        """
        Initialize batch-limited embeddings.

        Args:
            max_batch_size: Maximum texts per batch (default: 32)
            min_delay_seconds: Minimum delay between batches (default: 0.5s)
            max_concurrent: Maximum concurrent requests (default: 3)
            timeout: Request timeout in seconds (default: 30s)
        """
        self.max_batch_size = max_batch_size
        self.min_delay_seconds = min_delay_seconds
        self.max_concurrent = max_concurrent
        self.timeout = timeout

        # Semaphore for concurrent requests
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # Last request time for rate limiting
        self._last_request_time: float = 0.0

        # Lock for rate limiting
        self._rate_lock = asyncio.Lock()

    async def _wait_for_rate_limit(self) -> None:
        """Wait minimum delay since last request."""
        async with self._rate_lock:
            now = time.time()
            time_since_last = now - self._last_request_time
            if time_since_last < self.min_delay_seconds:
                wait_time = self.min_delay_seconds - time_since_last
                logger.debug(f"Rate limiting: waiting {wait_time:.3f}s")
                await asyncio.sleep(wait_time)
            self._last_request_time = time.time()

    async def embed_query(
        self,
        query: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> list[float]:
        """
        Generate embedding for a single query text.

        Args:
            query: Query string to embed
            http_client: Optional httpx AsyncClient to reuse

        Returns:
            Single embedding vector.
        """
        if not query.strip():
            raise ValueError("Cannot embed empty query")

        async with self._semaphore:
            await self._wait_for_rate_limit()
            results = await _embed_texts([query], timeout=self.timeout, http_client=http_client)
            return results[0]

    async def embed_texts(
        self,
        texts: list[str],
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> list[list[float]]:
        """
        Generate embeddings for multiple texts with automatic batching.

        Automatically splits large lists into batches that respect max_batch_size,
        processes them concurrently with rate limiting, and reassembles results.

        Args:
            texts: List of text strings to embed
            http_client: Optional httpx AsyncClient to reuse

        Returns:
            List of embedding vectors in the same order as input texts
        """
        if not texts:
            return []

        # If single batch needed, process directly
        if len(texts) <= self.max_batch_size:
            async with self._semaphore:
                await self._wait_for_rate_limit()
                return await _embed_texts(texts, timeout=self.timeout, http_client=http_client)

        # Split into batches
        batches = [
            texts[i:i + self.max_batch_size]
            for i in range(0, len(texts), self.max_batch_size)
        ]

        logger.info(
            f"Split {len(texts)} texts into {len(batches)} batches "
            f"(max_batch_size={self.max_batch_size})"
        )

        # Process batches concurrently with semaphore
        tasks: list[asyncio.Task[list[list[float]]]] = []
        for i, batch in enumerate(batches):
            async def process_batch(b: list[str], idx: int) -> list[list[float]]:
                async with self._semaphore:
                    await self._wait_for_rate_limit()
                    logger.debug(f"Processing batch {idx + 1}/{len(batches)} "
                               f"({len(b)} texts)")
                    result = await _embed_texts(b, timeout=self.timeout, http_client=http_client)
                    logger.debug(f"Completed batch {idx + 1}/{len(batches)}")
                    return result

            task = asyncio.create_task(process_batch(batch, i))
            tasks.append(task)

        # Wait for all batches to complete
        try:
            batch_results = await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Error processing batches: {e}")
            # Cancel remaining tasks
            for task in tasks:
                if not task.done():
                    task.cancel()
            raise

        # Reassemble results
        final_results: list[list[float]] = []
        for batch_result in batch_results:
            final_results.extend(batch_result)

        # Validate result count
        if len(final_results) != len(texts):
            logger.error(
                f"Result count mismatch: expected {len(texts)}, got {len(final_results)}"
            )
            raise ValueError(
                f"Embedding result count mismatch: expected {len(texts)}, got {len(final_results)}"
            )

        logger.info(f"Successfully embedded {len(final_results)} texts in {len(batches)} batches")
        return final_results

    async def close(self) -> None:
        """
        Clean up resources.

        Currently a no-op but kept for future extensibility
        (e.g., closing http_client if managed internally).
        """
        pass
