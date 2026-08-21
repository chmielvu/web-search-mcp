#!/usr/bin/env python3
"""Provider overlap, latency, and rewrite success analysis.

Adds a few extra tables/figures specifically aimed at diagnosing the
per-role and per-provider behaviour.

Outputs (tables/):
  - per_provider_health.csv       (per-provider call counts, success rate, results)
  - per_role_pairwise_overlap.csv (Jaccard between role result sets)
  - rewrite_perf.csv              (rewrite latency, errors, token usage by run)
  - candidate_to_final_promotion.csv
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import duckdb

ROOT = Path(__file__).parent
ART = ROOT / "artifacts"
TAB = ROOT / "tables"
FIG = ROOT / "figures"

DB = Path(
    r"C:\Users\Jan\Documents\GitHub\1Agents1\.CLI\web-search-mcp"
    r"\duckdb_data\analytics\search_events.duckdb"
)
con = duckdb.connect(str(DB), read_only=True)

# ---------------- Provider health ----------------
provider_calls = con.execute(
    """
    SELECT
        provider,
        COUNT(*) AS n_calls,
        SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS n_success,
        AVG(latency_ms) AS avg_ms,
        SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS n_errors
    FROM provider_calls
    GROUP BY provider
    ORDER BY n_calls DESC
    """
).fetch_df()
provider_calls["success_rate"] = (provider_calls["n_success"] / provider_calls["n_calls"]).round(3)
provider_calls.to_csv(TAB / "per_provider_health.csv", index=False)
print("Per-provider health (from provider_calls):")
print(provider_calls.to_string(index=False))

# ---------------- Rewrite performance ----------------
rewrite_perf = con.execute(
    """
    SELECT
        rewrite_model,
        COUNT(*) AS n_runs,
        AVG(rewrite_latency_ms) AS avg_latency_ms,
        SUM(CASE WHEN rewrite_error IS NULL OR rewrite_error = '' THEN 1 ELSE 0 END) AS n_success,
        SUM(CASE WHEN rewrite_error IS NOT NULL AND rewrite_error <> '' THEN 1 ELSE 0 END) AS n_error,
        AVG(rewrite_input_tokens) AS avg_in_tokens,
        AVG(rewrite_output_tokens) AS avg_out_tokens
    FROM search_runs
    WHERE rewrite_enabled = true
    GROUP BY rewrite_model
    ORDER BY n_runs DESC
    """
).fetch_df()
rewrite_perf.to_csv(TAB / "rewrite_perf.csv", index=False)
print("\nRewrite performance:")
print(rewrite_perf.to_string(index=False))

# ---------------- Per-role pairwise result overlap ----------------
# Use cands -> compute per run which providers contributed; then derive role
# Result sets per role via the branch_id -> run_key -> providers mapping
branches = pd.read_parquet(ART / "search_branches.parquet")
cands = pd.read_parquet(ART / "search_candidates.parquet")

# Build role -> set of candidate links per run
branches["providers_key"] = branches["attempted_providers"].apply(
    lambda lst: tuple(sorted(lst)) if isinstance(lst, (list, np.ndarray)) else tuple()
)
# For each (run_key, branch_role) the candidates from that branch are those
# whose providers intersect with the attempted_providers set.
# Since we don't have branch_id on cands, we approximate: result belongs to role R
# if all of its providers are a subset of attempted_providers[R] for that run.
role_links = defaultdict(set)  # (role, run_key) -> set(links)
for run_key, group in cands.groupby("run_key"):
    runs_branches = branches[branches["run_key"] == run_key]
    if runs_branches.empty:
        continue
    for _, brow in runs_branches.iterrows():
        role = brow["branch_role"]
        attempted_list = brow["attempted_providers"]
        if attempted_list is None or (isinstance(attempted_list, float) and pd.isna(attempted_list)):
            attempted = set()
        else:
            attempted = set(attempted_list)
        # A candidate belongs to a role if ANY of its providers is in role's
        # attempted_providers set. This is the correct interpretation since
        # the RRF merge combines across providers within a role.
        for _, crow in group.iterrows():
            cprovs_list = crow["providers"]
            if cprovs_list is None or (isinstance(cprovs_list, float) and pd.isna(cprovs_list)):
                cprovs = set()
            else:
                cprovs = set(cprovs_list)
            if cprovs and (cprovs & attempted):
                role_links[(role, run_key)].add(crow["link"])

# Compute pairwise Jaccard per role-pair across runs that have both
roles = sorted(branches["branch_role"].unique())
pair_rows = []
for i, a in enumerate(roles):
    for b in roles[i + 1 :]:
        # runs that have both
        a_runs = {rk for r, rk in role_links if r == a}
        b_runs = {rk for r, rk in role_links if r == b}
        common = a_runs & b_runs
        if not common:
            pair_rows.append({"role_a": a, "role_b": b, "jaccard_median": np.nan,
                              "overlap_median": np.nan, "n_common_runs": 0})
            continue
        jaccards, overlaps = [], []
        for rk in common:
            sa, sb = role_links[(a, rk)], role_links[(b, rk)]
            if not sa and not sb:
                continue
            j = len(sa & sb) / max(1, len(sa | sb))
            jaccards.append(j)
            overlaps.append(len(sa & sb) / max(1, len(sa)))
        pair_rows.append({
            "role_a": a, "role_b": b,
            "jaccard_median": float(np.median(jaccards)) if jaccards else np.nan,
            "overlap_median": float(np.median(overlaps)) if overlaps else np.nan,
            "n_common_runs": len(common),
        })
pair_df = pd.DataFrame(pair_rows)
pair_df.to_csv(TAB / "per_role_pairwise_overlap.csv", index=False)
print("\nPer-role pairwise result overlap (Jaccard median):")
print(pair_df.round(3).to_string(index=False))

# ---------------- Candidate → final promotion ----------------
finals = pd.read_parquet(ART / "final_results.parquet")
promotion = (
    finals.groupby("provider_count")
    .agg(
        n_finals=("link", "size"),
        avg_score=("final_score", "mean"),
        median_score=("final_score", "median"),
    )
    .reset_index()
)
promotion.to_csv(TAB / "candidate_to_final_promotion.csv", index=False)
print("\nFinal score by provider_count (multi-provider evidence):")
print(promotion.to_string(index=False))

# ---------------- Plots ----------------
plt.style.use("seaborn-v0_8-whitegrid")
FIG.mkdir(parents=True, exist_ok=True)

# 1. Provider success rate bar
fig, ax = plt.subplots(figsize=(10, 5))
sub = provider_calls.sort_values("success_rate")
ax.barh(sub["provider"], sub["success_rate"], color="steelblue")
ax.set_xlim(0, 1)
ax.set_xlabel("success_rate")
ax.set_title("Provider success rate (from provider_calls)")
fig.tight_layout()
fig.savefig(FIG / "provider_success_rate.png", dpi=140)
plt.close(fig)

# 2. Pairwise role overlap heatmap
mat = pd.DataFrame(np.nan, index=roles, columns=roles)
for _, r in pair_df.iterrows():
    mat.loc[r["role_a"], r["role_b"]] = r["jaccard_median"]
    mat.loc[r["role_b"], r["role_a"]] = r["jaccard_median"]
np.fill_diagonal(mat.values, 1.0)
fig, ax = plt.subplots(figsize=(7, 6))
im = ax.imshow(mat.values, cmap="YlOrRd", vmin=0, vmax=1)
ax.set_xticks(range(len(roles)))
ax.set_xticklabels(roles, rotation=20, ha="right")
ax.set_yticks(range(len(roles)))
ax.set_yticklabels(roles)
for i in range(len(roles)):
    for j in range(len(roles)):
        v = mat.values[i, j]
        if not np.isnan(v):
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=9)
fig.colorbar(im, ax=ax, label="Jaccard (median across runs)")
ax.set_title("Per-role result-set overlap (Jaccard median)")
fig.tight_layout()
fig.savefig(FIG / "per_role_pairwise_overlap.png", dpi=140)
plt.close(fig)

con.close()
print("\nDone.")
