#!/usr/bin/env python3
"""Intent drift analysis.

For each (input, branch) pair:
  1. Embed via TF-IDF on the union of input + all branch queries.
  2. Cluster the union with KMeans (k=15).
  3. For each pair, check whether input and branch end up in the same cluster.
  4. Compute "drift rate" = P(cluster(branch) != cluster(input)).
  5. Visualise the cluster confusion matrix (input cluster -> branch cluster).

Outputs (tables/):
  - intent_drift_per_role.csv
  - cluster_assignments.csv (input and branch nodes + cluster_id)
  - cluster_top_terms.csv
Figures (figures/):
  - intent_drift_per_role.png
  - cluster_confusion_heatmap.png
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer

ROOT = Path(__file__).parent
ART = ROOT / "artifacts"
TAB = ROOT / "tables"
FIG = ROOT / "figures"

pairs = pd.read_parquet(ART / "run_branch_pairs.parquet")
# Build normalized text columns
def _norm(s):
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return " ".join(str(s).split()).strip().lower()


pairs["input_norm"] = pairs["input_query"].map(_norm)
pairs["branch_norm"] = pairs["branch_query"].map(_norm)

# Build unique text set = input queries + branch queries
texts = pd.concat(
    [pairs["input_norm"].drop_duplicates(), pairs["branch_norm"].drop_duplicates()]
).dropna()
texts = texts[texts.str.len() > 0].tolist()
print(f"Total unique texts to embed: {len(texts)}")

vec = TfidfVectorizer(
    ngram_range=(1, 2),
    min_df=2,
    max_df=0.5,
    sublinear_tf=True,
    stop_words="english",
)
X = vec.fit_transform(texts)
print(f"TF-IDF shape: {X.shape}")

K = 15
km = KMeans(n_clusters=K, random_state=42, n_init=10)
labels = km.fit_predict(X)
text2label = dict(zip(texts, labels))

# Map pairs
pairs["input_cluster"] = pairs["input_norm"].map(text2label)
pairs["branch_cluster"] = pairs["branch_norm"].map(text2label)
pairs["drift"] = (pairs["input_cluster"] != pairs["branch_cluster"]).astype(int)

per_role = (
    pairs.groupby("branch_role")
    .agg(
        n=("run_key", "size"),
        drift_rate=("drift", "mean"),
        same_rate=("drift", lambda s: 1 - s.mean()),
    )
    .reset_index()
    .sort_values("drift_rate", ascending=False)
)
per_role.to_csv(TAB / "intent_drift_per_role.csv", index=False)
print("\nIntent drift per role:")
print(per_role.to_string(index=False))

# Cluster top terms
terms = np.array(vec.get_feature_names_out())
cluster_top = []
for c in range(K):
    centroid = km.cluster_centers_[c]
    top_idx = np.argsort(-centroid)[:10]
    cluster_top.append(
        {"cluster_id": c, "size": int((labels == c).sum()), "top_terms": ", ".join(terms[top_idx])}
    )
ctdf = pd.DataFrame(cluster_top)
ctdf.to_csv(TAB / "cluster_top_terms.csv", index=False)
print("\nCluster top terms:")
print(ctdf.to_string(index=False))

# Confusion: how do input cluster -> branch cluster transitions look?
# Build a matrix of size (K+1, K+1) where last row/col = "self"
sub = pairs.dropna(subset=["input_cluster", "branch_cluster"]).copy()
sub["input_cluster"] = sub["input_cluster"].astype(int)
sub["branch_cluster"] = sub["branch_cluster"].astype(int)
conf = np.zeros((K, K), dtype=int)
for r in sub.itertuples(index=False):
    conf[r.input_cluster, r.branch_cluster] += 1
# Normalize per input cluster (i.e. row)
row_sums = conf.sum(axis=1, keepdims=True)
conf_norm = np.divide(conf, row_sums, out=np.zeros_like(conf, dtype=float), where=row_sums > 0)
np.fill_diagonal(conf_norm, conf_norm.diagonal())  # keep self
fig, ax = plt.subplots(figsize=(10, 8))
im = ax.imshow(conf_norm, cmap="YlOrRd", vmin=0, vmax=0.5)
ax.set_xticks(range(K))
ax.set_xticklabels(range(K), rotation=0)
ax.set_yticks(range(K))
ax.set_yticklabels(range(K))
ax.set_xlabel("branch cluster")
ax.set_ylabel("input cluster")
ax.set_title("Cluster transition: input -> branch (row-normalised)")
fig.colorbar(im, ax=ax, label="P(branch cluster | input cluster)")
fig.tight_layout()
fig.savefig(FIG / "cluster_confusion_heatmap.png", dpi=140)
plt.close(fig)

# Bar plot of drift per role
fig, ax = plt.subplots(figsize=(8, 4))
ax.bar(per_role["branch_role"], per_role["drift_rate"], color="indianred")
ax.set_ylabel("drift rate (cluster(input) != cluster(branch))")
ax.set_title("Intent drift per branch role")
plt.xticks(rotation=20, ha="right")
fig.tight_layout()
fig.savefig(FIG / "intent_drift_per_role.png", dpi=140)
plt.close(fig)

# Save cluster assignments for all unique nodes
all_nodes = (
    pd.DataFrame({"text": texts, "cluster_id": labels})
    .assign(
        node_type=lambda d: d["text"].isin(set(pairs["input_norm"])).map({True: "input", False: "rewrite"})
    )
)
all_nodes.to_csv(TAB / "cluster_assignments.csv", index=False)

# Write a top-15 'most-drifted-to' transitions for inspection
drift_targets = (
    sub.groupby(["input_cluster", "branch_cluster"]).size().reset_index(name="count")
    .sort_values("count", ascending=False)
    .head(20)
)
drift_targets.to_csv(TAB / "top_drift_transitions.csv", index=False)
print("\nTop drift transitions:")
print(drift_targets.to_string(index=False))

# Per-intent drift too
per_intent = (
    pairs.dropna(subset=["input_cluster", "branch_cluster"])
    .groupby("intent")
    .agg(
        n=("run_key", "size"),
        drift_rate=("drift", "mean"),
    )
    .reset_index()
    .sort_values("drift_rate", ascending=False)
)
per_intent.to_csv(TAB / "intent_drift_per_intent.csv", index=False)
print("\nIntent drift per intent:")
print(per_intent.to_string(index=False))

print("\nDone.")
