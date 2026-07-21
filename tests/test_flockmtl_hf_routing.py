"""Regression tests for the HF-router swap in `analytics/writers/connection.py`.

Guarantees:
1. `_FLOCKMTL_MODEL_DDL` points at the exact two HF-routed IDs (no Mistral drift).
2. `_ensure_flockmtl_secret` reads `HF_TOKEN` (HF only — no Mistral fallback,
   because Mistral creds get 401 from `router.huggingface.co`).
3. `_upsert_flockmtl_model` UPDATEs in place when present, falls through to
   CREATE on the standard `Model 'X' doesn't exist.` error, and re-raises
   on any other duckdb.Error.

`HF_TOKEN` is a test sentinel; the assertion that proves the env var was
the auth source compares against the sentinel VALUE (nonsecret by
definition — `monkeypatch.setenv` substitutes it). Real CI secrets
never interpolate into these tests.
"""

from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

import duckdb
import pytest

from kindly_web_search_mcp_server.analytics.writers import connection as flock_conn


SENTINEL_HF_TOKEN = "hf_test_sentinel_value"
EXPECTED_BASE_URL = "https://router.huggingface.co/v1"


@pytest.fixture
def fake_conn() -> MagicMock:
    fake = MagicMock(name="FakeDuckDBConn")
    fake.executed_sql: List[str] = []
    fake.executed_effects: List[object] = []

    def _execute(sql: str, *_args, **_kwargs):
        fake.executed_sql.append(sql)
        if fake.executed_effects:
            effect = fake.executed_effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            return effect
        return MagicMock()

    fake.execute.side_effect = _execute
    return fake


def test_flockmtl_model_ddl_contains_exact_hf_ids() -> None:
    ddl = {name: mid for name, mid, _ in flock_conn._FLOCKMTL_MODEL_DDL}
    assert ddl == {
        "judge_fast": "Qwen/Qwen3-4B-Instruct-2507:nscale",
        "judge_quality": "deepseek-ai/DeepSeek-V4-Flash:deepinfra",
    }


def test_flockmtl_model_ddl_provider_is_openai() -> None:
    providers = {name: prov for name, _, prov in flock_conn._FLOCKMTL_MODEL_DDL}
    assert providers == {"judge_fast": "openai", "judge_quality": "openai"}


def test_ensure_flockmtl_secret_emits_hf_token_and_router_url(monkeypatch, fake_conn) -> None:
    monkeypatch.setenv("HF_TOKEN", SENTINEL_HF_TOKEN)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    flock_conn._ensure_flockmtl_secret(fake_conn)

    assert len(fake_conn.executed_sql) == 2
    drop_stmt, create_stmt = fake_conn.executed_sql
    assert drop_stmt == "DROP SECRET IF EXISTS __default_openai"
    assert "CREATE SECRET __default_openai" in create_stmt
    assert "TYPE OPENAI" in create_stmt
    assert EXPECTED_BASE_URL in create_stmt
    assert f"API_KEY '{SENTINEL_HF_TOKEN}'" in create_stmt
    # env var name must never appear (secrets are values, not references)
    assert "HF_TOKEN" not in create_stmt


def test_ensure_flockmtl_secret_rejects_mistral_fallback(monkeypatch, fake_conn) -> None:
    """HF_TOKEN absent + MISTRAL_API_KEY present: no secret ops, no leakage."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("MISTRAL_API_KEY", "test_mistral_should_be_ignored")

    flock_conn._ensure_flockmtl_secret(fake_conn)

    assert fake_conn.executed_sql == []


def test_ensure_flockmtl_secret_missing_both_envs(monkeypatch, fake_conn) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    flock_conn._ensure_flockmtl_secret(fake_conn)
    assert fake_conn.executed_sql == []


def test_upsert_flockmtl_model_updates_existing_alias_in_place(fake_conn) -> None:
    result = flock_conn._upsert_flockmtl_model(
        fake_conn, "judge_quality", "deepseek-ai/DeepSeek-V4-Flash:deepinfra", "openai"
    )
    assert result == "updated"
    assert len(fake_conn.executed_sql) == 1
    (stmt,) = fake_conn.executed_sql
    assert "UPDATE MODEL" in stmt
    assert "'judge_quality'" in stmt
    assert "DELETE" not in stmt  # UPDATE-in-place must NOT include DELETE


def test_upsert_flockmtl_model_creates_when_alias_absent(fake_conn) -> None:
    """Real duckdb.Error with the standard 'doesn't exist' text → CREATE.

    The first execute() is programmed to raise a REAL duckdb.Error
    instance so production's `except duckdb.Error` clause catches it.
    """
    fake_conn.executed_effects.append(
        duckdb.Error("Invalid Error: Model 'judge_fast' doesn't exist.")
    )
    result = flock_conn._upsert_flockmtl_model(
        fake_conn, "judge_fast", "Qwen/Qwen3-4B-Instruct-2507:nscale", "openai"
    )
    assert result == "created"
    assert len(fake_conn.executed_sql) == 2
    update_stmt, create_stmt = fake_conn.executed_sql
    assert "UPDATE MODEL" in update_stmt
    assert "CREATE MODEL" in create_stmt


def test_upsert_flockmtl_model_propagates_non_missing_errors(fake_conn) -> None:
    """Non-missing errors must surface (catalogue locks etc.); not be swallowed."""
    fake_conn.executed_effects.append(duckdb.Error("Catalog lock contention on model catalog."))
    with pytest.raises(duckdb.Error, match="Catalog lock"):
        flock_conn._upsert_flockmtl_model(
            fake_conn, "judge_quality", "deepseek-ai/DeepSeek-V4-Flash:deepinfra", "openai"
        )
    # Only the failed UPDATE was attempted; CREATE was NOT issued.
    assert len(fake_conn.executed_sql) == 1
