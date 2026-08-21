#!/usr/bin/env python3
"""Result quality and distribution analysis.

For each successful run:
- How many final results, candidates?
- How does rank order correlate with final_score?
- What is provider overlap per result (single vs multi-provider)?
- How much overlap is there between the rewrites' candidate pools?
- Top domains, top duplicate domains.
- Anomaly detection: empty results, low scores, identical result sets across branches.

Outputs (tables/):
  - final_result_distribution.csv
  - result_quality_per_intent.csv
  - top_domains.csv
  - provider_overlap.csv
  - per_role_overlap.csv
  - anomalies.csv

Figures (figures/):
  - final_result_count_distribution.png
  - final_score_distribution.png
  - rank_vs_score.png
  - top_domains.png
  - per_role_provider_count.png
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
ART = ROOT / "artifacts"
TAB = ROOT / "tables"
FIG = ROOT / "figures"
TAB.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)

runs = pd.read_parquet(ART / "search_runs.parquet")
branches = pd.read_parquet(ART / "search_branches.parquet")
finals = pd.read_parquet(ART / "final_results.parquet")
cands = pd.read_parquet(ART / "search_candidates.parquet")

# ---------------- 1. Final result count distribution ----------------
success = runs[runs["status"] == "success"].copy()
final_per_run = finals.groupby("run_key").size().rename("final_count")
cand_per_run = cands.groupby("run_key").size().rename("candidate_count")

dist = success.merge(final_per_run, left_on="run_key", right_index=True, how="left").merge(
    cand_per_run.rename("cand_pool_size"),
    left_on="run_key",
    right_index=True,
    how="left",
)
dist[["final_count", "cand_pool_size"]] = dist[["final_count", "cand_pool_size"]].fillna(0).astype(int)
dist = dist.rename(columns={"candidate_count": "candidate_count_reported"})
dist["final_count"].to_csv(TAB / "final_result_distribution.csv", index=False)

print("Final results per run (success only):")
print(dist["final_count"].describe().to_string())
print("\n# runs with 0 final results:", (dist["final_count"] == 0).sum())
print("# runs with <5 final results:", (dist["final_count"] < 5).sum())
print("# runs with >=10 final results:", (dist["final_count"] >= 10).sum())

# ---------------- 2. Quality per intent ----------------
per_intent = (
    dist.groupby("intent")
    .agg(
        n=("run_key", "size"),
        final_median=("final_count", "median"),
        final_mean=("final_count", "mean"),
        final_p25=("final_count", lambda s: float(np.percentile(s, 25))),
        final_p75=("final_count", lambda s: float(np.percentile(s, 75))),
        cand_median=("cand_pool_size", "median"),
        cand_mean=("cand_pool_size", "mean"),
    )
    .reset_index()
)
per_intent.to_csv(TAB / "result_quality_per_intent.csv", index=False)
print("\nResults per intent:")
print(per_intent.to_string(index=False))

# ---------------- 3. Top domains ----------------
domain_counts = (
    finals["domain"]
    .fillna("(unknown)")
    .value_counts()
    .head(40)
    .rename_axis("domain")
    .reset_index(name="appearances")
)
domain_counts.to_csv(TAB / "top_domains.csv", index=False)

# ---------------- 4. Provider overlap per result ----------------
finals["provider_count_filled"] = finals["provider_count"].fillna(0).astype(int)
prov_overlap = (
    finals["provider_count_filled"]
    .value_counts()
    .rename_axis("provider_count")
    .reset_index(name="n_results")
    .sort_values("provider_count")
)
prov_overlap["pct"] = (prov_overlap["n_results"] / prov_overlap["n_results"].sum() * 100).round(2)
prov_overlap.to_csv(TAB / "provider_overlap.csv", index=False)
print("\nProvider overlap distribution (# providers per result):")
print(prov_overlap.to_string(index=False))

# ---------------- 5. Per-role provider count comparison ----------------
# per branch, count distinct providers in attempted_providers
branches["attempted_n"] = branches["attempted_providers"].apply(
    lambda lst: len(lst) if isinstance(lst, (list, np.ndarray)) else 0
)
branches["assigned_n"] = branches["assigned_providers"].apply(
    lambda lst: len(lst) if isinstance(lst, (list, np.ndarray)) else 0
)
branches["skipped_n"] = branches["skipped_providers"].apply(
    lambda lst: len(lst) if isinstance(lst, (list, np.ndarray)) else 0
)
role_summary = (
    branches.groupby("branch_role")
    .agg(
        n=("run_key", "size"),
        results_median=("results_count", "median"),
        results_mean=("results_count", "mean"),
        assigned_median=("assigned_n", "median"),
        attempted_median=("attempted_n", "median"),
        skipped_median=("skipped_n", "median"),
        latency_median_ms=("latency_ms", "median"),
        latency_mean_ms=("latency_ms", "mean"),
    )
    .reset_index()
)
role_summary.to_csv(TAB / "per_role_summary.csv", index=False)
print("\nPer-role summary:")
print(role_summary.to_string(index=False))

# ---------------- 6. Final score analysis ----------------
print("\nFinal score stats:")
print(finals["final_score"].describe().to_string())
print("Final score null %:", round(finals["final_score"].isna().mean() * 100, 2))

# ---------------- 7. Anomaly detection ----------------
anomalies = []
# empty results
empty = dist[dist["final_count"] == 0][["run_key", "intent", "cand_pool_size", "status"]]
if not empty.empty:
    for r in empty.itertuples(index=False):
        anomalies.append({"run_key": r.run_key, "anomaly": "zero_final_results", "detail": f"intent={r.intent}, cands={r.cand_pool_size}"})
# low score
low_score_runs = (
    finals.groupby("run_key")["final_score"].max().rename("max_score").reset_index()
)
low = low_score_runs[low_score_runs["max_score"] < 0.05]
for r in low.itertuples(index=False):
    anomalies.append({"run_key": r.run_key, "anomaly": "all_low_scores", "detail": f"max_score={r.max_score:.4f}"})
# identical sets across branches (within same run): low diversity
# Group by run and check the union of final result domains
def dom_set(series):
    return frozenset(series.dropna().tolist())


dom_per_run = finals.groupby("run_key")["domain"].apply(dom_set).reset_index()
# No clean per-branch attribution available, so leave as a simple diversity metric
# instead measure: how many runs have only 1 unique domain
single_domain_runs = dom_per_run[dom_per_run["domain"].apply(len) <= 1]
for r in single_domain_runs.itertuples(index=False):
    anomalies.append({"run_key": r.run_key, "anomaly": "single_domain_results", "detail": f"unique_domains={len(r.domain)}"})

adf = pd.DataFrame(anomalies)
adf.to_csv(TAB / "anomalies.csv", index=False)
print(f"\nAnomalies detected: {len(anomalies)}")
print(adf.head(20).to_string())

# ---------------- Figures ----------------
plt.style.use("seaborn-v0_8-whitegrid")

# Final result count distribution
fig, ax = plt.subplots(figsize=(8, 4))
ax.hist(dist["final_count"], bins=range(0, dist["final_count"].max() + 2), edgecolor="black")
ax.set_xlabel("# final results per run")
ax.set_ylabel("# runs")
ax.set_title(f"Final result count distribution (n={len(dist)} runs)")
fig.tight_layout()
fig.savefig(FIG / "final_result_count_distribution.png", dpi=140)
plt.close(fig)

# Final score distribution
fig, ax = plt.subplots(figsize=(8, 4))
scores = finals["final_score"].dropna()
ax.hist(scores, bins=30, edgecolor="black", color="steelblue")
ax.set_xlabel("final_score")
ax.set_ylabel("# final results")
ax.set_title(f"final_score distribution (n={len(scores)})")
fig.tight_layout()
fig.savefig(FIG / "final_score_distribution.png", dpi=140)
plt.close(fig)

# Rank vs score
fig, ax = plt.subplots(figsize=(8, 4))
ax.scatter(finals["rank"], finals["final_score"], s=8, alpha=0.4, color="indianred")
ax.set_xlabel("rank")
ax.set_ylabel("final_score")
ax.set_title("final_score vs rank")
fig.tight_layout()
fig.savefig(FIG / "rank_vs_score.png", dpi=140)
plt.close(fig)

# Top domains bar
fig, ax = plt.subplots(figsize=(9, 6))
top = domain_counts.head(20)
ax.barh(top["domain"][::-1], top["appearances"][::-1], color="steelblue")
ax.set_xlabel("appearances")
ax.set_title("Top 20 final-result domains")
fig.tight_layout()
fig.savefig(FIG / "top_domains.png", dpi=140)
plt.close(fig)

# Per-role provider count
fig, ax = plt.subplots(figsize=(9, 4))
roles = role_summary["branch_role"].tolist()
xs = np.arange(len(roles))
w = 0.27
ax.bar(xs - w, role_summary["assigned_median"], w, label="assigned (median)", color="#888")
ax.bar(xs, role_summary["attempted_median"], w, label="attempted (median)", color="steelblue")
ax.bar(xs + w, role_summary["results_median"], w, label="results (median)", color="indianred")
ax.set_xticks(xs)
ax.set_xticklabels(roles, rotation=20, ha="right")
ax.set_ylabel("count")
ax.set_title("Per-role provider & result counts (median)")
ax.legend()
fig.tight_layout()
fig.savefig(FIG / "per_role_provider_count.png", dpi=140)
plt.close(fig)

# ---------------- 8. Per-run result overlap between branches ----------------
# The candidates table is provider-scoped (no branch_id), but we can compute
# result-set overlap across runs: how many distinct final domains per run?
# That's a measure of diversity rather than per-branch overlap.
div_per_run = finals.groupby("run_key").agg(
    n_results=("link", "size"),
    n_unique_links=("link", "nunique"),
    n_unique_domains=("domain", "nunique"),
).reset_index()
div_per_run["dup_links"] = div_per_run["n_results"] - div_per_run["n_unique_links"]
div_per_run["link_diversity"] = (div_per_run["n_unique_links"] / div_per_run["n_results"].clip(lower=1)).round(3)
div_per_run["domain_diversity"] = (div_per_run["n_unique_domains"] / div_per_run["n_results"].clip(lower=1)).round(3)
div_per_run.to_csv(TAB / "per_run_diversity.csv", index=False)

print("\nPer-run result diversity:")
print(div_per_run[["n_results", "n_unique_links", "n_unique_domains", "link_diversity", "domain_diversity"]].describe().to_string())

print("\nAll result quality tables/figures written.")
