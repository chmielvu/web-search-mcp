"""PROTOTYPE TUI — throwaway shell over the in-memory DuckDB schema model.

Run: uv run python -m kindly_web_search_mcp_server.analytics._prototype_schema_tui
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from ._prototype_schema_model import (
        VIEWS,
        SchemaState,
        counts,
        integrity_summary,
        reduce,
        view_rows,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _prototype_schema_model import (  # type: ignore
        VIEWS,
        SchemaState,
        counts,
        integrity_summary,
        reduce,
        view_rows,
    )

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


def _read_key() -> str:
    try:
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()
            return ""
        return ch
    except ImportError:
        line = input()
        return line[:1] if line else ""


def _dump(obj: object) -> str:
    return json.dumps(obj, indent=2, default=str)


def render(state: SchemaState) -> str:
    fact_counts = counts(state)
    integrity = integrity_summary(state)
    rows = view_rows(state)
    lines = [
        f"{BOLD}DuckDB analytics schema prototype{RESET}  {DIM}in-memory; no production writes{RESET}",
        f"{DIM}facts -> normalized lineage -> executable views; vectors remain exact-scan by default{RESET}",
        "",
        f"{BOLD}note{RESET}  {state.note}",
        f"{DIM}last={state.last_action}  clock={state.clock_ms}ms  view={state.view}{RESET}",
        "",
        f"{BOLD}fact counts{RESET}",
        "  "
        f"runs={fact_counts['search_runs']} variants={fact_counts['query_variants']} "
        f"branches={fact_counts['search_branches']} provider_calls={fact_counts['provider_calls']} "
        f"provider_results={fact_counts['provider_results']}",
        "  "
        f"candidates={fact_counts['search_candidates']} stage_runs={fact_counts['rerank_stage_executions']} "
        f"stage_events={fact_counts['candidate_stage_events']} finals={fact_counts['final_results']}",
        "  "
        f"tool_events={fact_counts['tool_events']} output_items={fact_counts['tool_output_items']} "
        f"fetches={fact_counts['content_fetches']} judgments={fact_counts['judgment_facets']} "
        f"vectors={fact_counts['query_embeddings'] + fact_counts['candidate_embeddings']}",
        f"  derived_invocations={integrity['tool_invocations']} integrity_issues={integrity['issues']}",
        "",
        f"{BOLD}view: {state.view}{RESET}",
        _dump(rows),
        "",
        f"{BOLD}scenario keys{RESET}",
        f"  {DIM}[1]{RESET} retrieval lineage   {DIM}[2]{RESET} skip bi   "
        f"{DIM}[3]{RESET} cross filter   {DIM}[4]{RESET} RankLLM fail-open   {DIM}[5]{RESET} finalize",
        f"  {DIM}[6]{RESET} quick tool path    {DIM}[7]{RESET} fetch lineage   "
        f"{DIM}[8]{RESET} facet judgments   {DIM}[9]{RESET} lifecycle anomalies",
        f"  {DIM}[x]{RESET} drop-all/no-resurrection adversary",
        f"  {DIM}[v]{RESET} next view ({', '.join(VIEWS)})   {DIM}[0]{RESET} reset   {DIM}[q]{RESET} quit",
    ]
    return "\n".join(lines)


def main() -> None:
    state = SchemaState()
    try:
        while True:
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write(render(state))
            sys.stdout.write("\n")
            sys.stdout.flush()
            key = _read_key()
            if key in ("q", "Q"):
                return
            if not key:
                continue
            state = reduce(state, key.lower())
    finally:
        state.close()


if __name__ == "__main__":
    main()
