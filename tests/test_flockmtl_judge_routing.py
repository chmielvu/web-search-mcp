"""Regression tests for the judge-chain routing swap (2026-08-22).

Guarantees pinned here:
1. `_FLOCKMTL_MODEL_DDL` resolves both aliases to the NanoGPT-served
   fallback model (no Hugging Face / Mistral drift).
2. `_ensure_flockmtl_secret` reads `NANOGPT_API_KEY` (via settings) and
   emits the NanoGPT base URL; no secret ops when the key is absent;
   HF_TOKEN is never consulted.
3. `_upsert_flockmtl_model` UPDATE-in-place / CREATE-fallback semantics
   survive the provider swap unchanged.

The sentinel key value is nonsecret by definition — `monkeypatch`
substitutes it; real CI secrets never interpolate into these tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import duckdb
import pytest

from kindly_web_search_mcp_server.analytics.writers import connection as flock_conn
from kindly_web_search_mcp_server.settings import settings

SENTINEL_NANO_KEY = "nano_test_sentinel_value"
EXPECTED_BASE_URL = "https://nano-gpt.com/api/subscription/v1"
EXPECTED_FALLBACK_ID = "deepseek/deepseek-v4-flash-0731:thinking"


@pytest.fixture
def fake_conn() -> MagicMock:
    """MagicMock conn whose execute() records every SQL statement."""
    fake = MagicMock(name="FakeDuckDBConn")
    fake.executed_sql = []

    def _record(sql, *_args, **_kwargs):
        fake.executed_sql.append(sql)
        return None

    fake.execute.side_effect = _record
    return fake


def test_flockmtl_model_ddl_points_at_nanogpt_fallback() -> None:
    ddl = {name: mid for name, mid, _ in flock_conn._FLOCKMTL_MODEL_DDL}
    assert ddl == {
        "judge_fast": EXPECTED_FALLBACK_ID,
        "judge_quality": EXPECTED_FALLBACK_ID,
    }


def test_flockmtl_model_ddl_provider_is_openai() -> None:
    providers = {name: prov for name, _, prov in flock_conn._FLOCKMTL_MODEL_DDL}
    assert providers == {"judge_fast": "openai", "judge_quality": "openai"}


def test_ensure_flockmtl_secret_emits_nano_key_and_url(monkeypatch, fake_conn) -> None:
    monkeypatch.setattr(settings, "nano_gpt_api_key", SENTINEL_NANO_KEY)
    monkeypatch.delenv("HF_TOKEN", raising=False)

    flock_conn._ensure_flockmtl_secret(fake_conn)

    assert len(fake_conn.executed_sql) == 2
    drop_stmt, create_stmt = fake_conn.executed_sql
    assert "DROP SECRET" in drop_stmt
    assert EXPECTED_BASE_URL in create_stmt
    assert f"API_KEY '{SENTINEL_NANO_KEY}'" in create_stmt
    # The env var name must never appear (secrets are values, not references)
    # and no Hugging Face material may leak into the wire DDL.
    assert "HF_TOKEN" not in create_stmt
    assert "router.huggingface.co" not in create_stmt


def test_ensure_flockmtl_secret_missing_key_no_ops(monkeypatch, fake_conn) -> None:
    monkeypatch.setattr(settings, "nano_gpt_api_key", "")
    monkeypatch.setenv("HF_TOKEN", "must_be_ignored")

    flock_conn._ensure_flockmtl_secret(fake_conn)

    assert fake_conn.executed_sql == []


def test_upsert_flockmtl_model_updates_existing_alias_in_place(fake_conn) -> None:
    result = flock_conn._upsert_flockmtl_model(
        fake_conn, "judge_quality", EXPECTED_FALLBACK_ID, "openai"
    )
    assert result == "updated"
    assert len(fake_conn.executed_sql) == 1
    stmt = fake_conn.executed_sql[0]
    assert "UPDATE MODEL" in stmt
    assert "DELETE" not in stmt  # UPDATE-in-place must NOT include DELETE


def test_upsert_flockmtl_model_creates_when_alias_absent() -> None:
    """Real duckdb.Error with the standard "doesn't exist" text -> CREATE."""
    fake = MagicMock(name="FakeDuckDBConn")
    fake.execute.side_effect = [
        duckdb.Error("Model 'judge_quality' doesn't exist."),
        None,
    ]

    result = flock_conn._upsert_flockmtl_model(
        fake, "judge_quality", EXPECTED_FALLBACK_ID, "openai"
    )

    assert result == "created"
    assert fake.execute.call_count == 2
    assert "CREATE MODEL" in fake.execute.call_args_list[1].args[0]


def test_upsert_flockmtl_model_propagates_non_missing_errors() -> None:
    """Non-missing errors must surface (catalogue locks etc.), not be swallowed."""
    fake = MagicMock(name="FakeDuckDBConn")
    fake.execute.side_effect = duckdb.Error("Catalog lock contention on model catalog.")

    with pytest.raises(duckdb.Error, match="Catalog lock"):
        flock_conn._upsert_flockmtl_model(fake, "judge_quality", EXPECTED_FALLBACK_ID, "openai")
    # Only the failed UPDATE was attempted; CREATE was NOT issued.
    assert fake.execute.call_count == 1
