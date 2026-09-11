#!/usr/bin/env python3
"""Graph & network analysis with networkx.

Builds:
  1) Q -> R (input query -> rewrite) multi-graph
  2) R -> L (rewrite -> final result link) bipartite
  3) Q -> L (input query -> final result link) bipartite
  4) Q -> R -> L combined flow graph

Computes:
  - Node counts, edge counts, density
  - Degree centrality (top hubs in each projection)
  - Community detection (greedy modularity) on the Q-R-Q projection
  - Bipartite node overlap, weighted projection
  - Top hub queries (most connected to results)

Outputs (tables/):
  - graph_summary.csv
  - top_hub_queries.csv
  - top_hub_rewrites.csv
  - top_hub_results.csv
  - q_l_jaccard_topk.csv
  - communities.csv

Figures (figures/):
  - degree_centrality_top_queries.png
  - degree_centrality_top_results.png
  - jaccard_heatmap_qq.png
  - community_size_distribution.png
  - graph_skill_layout.png (a small subgraph)
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
from networkx.algorithms import bipartite
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.cluster import KMeans

ROOT = Path(__file__).parent
ART = ROOT / "artifacts"
TAB = ROOT / "tables"
FIG = ROOT / "figures"
TAB.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)

pairs = pd.read_parquet(ART / "run_branch_pairs.parquet")
finals = pd.read_parquet(ART / "final_results.parquet")

# Unique key normalization
def qkey(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return " ".join(str(s).split()).strip().lower()


pairs["input_key"] = pairs["input_query"].map(qkey)
pairs["branch_key"] = pairs["branch_query"].map(qkey)
finals["input_key"] = finals["run_key"].map(lambda k: k)  # not used; we'll join via run_key

# ------------------ 1. Q -> R graph (input queries -> rewrites) ------------------
# collapse identical (input, branch) across runs, attribute count and roles
qr_edges = (
    pairs.groupby(["input_key", "branch_key", "branch_role"])
    .size()
    .reset_index(name="weight")
)

G_qr = nx.DiGraph()
for inp, br, role, w in qr_edges[["input_key", "branch_key", "branch_role", "weight"]].itertuples(index=False):
    if inp == br:
        continue  # skip self-loops for cleaner analysis
    G_qr.add_edge(inp, br, role=role, weight=int(w))
# Add self-loops as a separate attribute
self_loops = (
    pairs[pairs["input_key"] == pairs["branch_key"]]
    .groupby(["input_key", "branch_role"])
    .size()
    .reset_index(name="weight")
)
for inp, role, w in self_loops.itertuples(index=False):
    if G_qr.has_edge(inp, inp):
        G_qr.edges[inp, inp]["self_role_count"] = G_qr.edges[inp, inp].get("self_role_count", 0) + int(w)
        G_qr.edges[inp, inp]["self_roles"] = G_qr.edges[inp, inp].get("self_roles", set()) | {role}
    else:
        G_qr.add_edge(inp, inp, role="self", weight=int(w),
                      self_role_count=int(w), self_roles={role})

# ------------------ 2. R -> L (rewrite -> result) ------------------
# We don't have branch_id in final_results directly; but branch_id IS in candidates
# For the final link bipartite, attach runs to results via run_key.
# We treat each (branch_query, final result link) as an edge if the branch contributed
# to the run that produced that final result.
# Without per-result branch attribution, use run-level provenance: an edge exists
# between a branch_query and a final result if (run_key of branch) == (run_key of result)
# and the link appears in the final_results for that run.
branches = pd.read_parquet(ART / "search_branches.parquet")
branches["branch_key"] = branches["branch_query"].map(qkey)
branches["input_key"] = branches["run_key"].map(
    lambda k: qkey(pairs.loc[pairs["run_key"] == k, "input_query"].iloc[0]) if (pairs["run_key"] == k).any() else ""
)

# We need to know which branch_id is "selected" for which final result. Search candidates
# has provider info. The "canonical_result_id" is shared between final_results and
# search_candidates. We can use that to map candidate -> final result.
cands = pd.read_parquet(ART / "search_candidates.parquet")

# Build a branch -> run -> links map
run_links = finals.groupby("run_key")["link"].apply(list).to_dict()

# Per-branch run -> candidates that became finals
run_cands_promoted = (
    finals[["run_key", "canonical_result_id"]]
    .dropna()
    .assign(promoted=1)
)

cands_promoted = cands.merge(
    run_cands_promoted, on=["run_key", "canonical_result_id"], how="inner"
)

branch_results = (
    branches[["run_key", "branch_key", "branch_role", "branch_index"]]
    .merge(cands_promoted[["run_key", "link", "domain", "rrf_score"]], on="run_key", how="inner")
)

# Edge: (branch_query, link)
rl_edges = (
    branch_results.groupby(["branch_key", "link"])
    .agg(
        weight=("link", "size"),
        avg_rrf=("rrf_score", "mean"),
    )
    .reset_index()
)

# ------------------ 3. Q -> L (input query -> final result) ------------------
# Aggregate: input query -> final result link
runs_meta = pairs.drop_duplicates("run_key")[["run_key", "input_query"]].copy()
finals_with_input = finals.merge(runs_meta, on="run_key", how="left")
finals_with_input["input_key"] = finals_with_input["input_query"].map(qkey)
ql_edges = (
    finals_with_input.groupby(["input_key", "link"])
    .agg(
        weight=("link", "size"),
        avg_final_score=("final_score", "mean"),
        avg_provider_count=("provider_count", "mean"),
    )
    .reset_index()
)

# ------------------ 4. combined Q -> R -> L flow ------------------
G_flow = nx.DiGraph()
for inp, br, role, w in qr_edges[["input_key", "branch_key", "branch_role", "weight"]].itertuples(index=False):
    if inp == br:
        # still add but mark self
        G_flow.add_edge(inp, br, role=role, weight=int(w), kind="self")
    else:
        G_flow.add_edge(inp, br, role=role, weight=int(w), kind="rewrite")
for inp, link, w, score in ql_edges[["input_key", "link", "weight", "avg_final_score"]].itertuples(index=False):
    G_flow.add_edge(inp, link, weight=int(w), avg_final_score=float(score), kind="final")

# ------------------ summaries ------------------
def safe_topk(deg: dict, k: int) -> list:
    return sorted(deg.items(), key=lambda kv: kv[1], reverse=True)[:k]


# Undirected version for undirected metrics
Gu = G_qr.to_undirected()
in_deg = dict(G_qr.in_degree())
out_deg = dict(G_qr.out_degree())
Gu_deg = dict(Gu.degree())

# --- Top hub queries (most out-degree = most rewrites) ---
top_q = safe_topk(out_deg, 30)
top_r = safe_topk(in_deg, 30)
top_q_shared = safe_topk(Gu_deg, 30)

pd.DataFrame(top_q, columns=["input_query", "out_degree"]).to_csv(TAB / "top_hub_queries.csv", index=False)
pd.DataFrame(top_r, columns=["rewrite", "in_degree"]).to_csv(TAB / "top_hub_rewrites.csv", index=False)
pd.DataFrame(top_q_shared, columns=["node", "undirected_degree"]).to_csv(TAB / "top_hub_undirected.csv", index=False)

# --- top hub final results by indegree in Q->L projection ---
G_ql = nx.DiGraph()
for inp, link, w, score in ql_edges[["input_key", "link", "weight", "avg_final_score"]].itertuples(index=False):
    G_ql.add_edge(inp, link, weight=int(w), avg_final_score=float(score))
indeg_l = dict(G_ql.in_degree())
top_links = safe_topk(indeg_l, 30)
pd.DataFrame(top_links, columns=["link", "in_degree"]).to_csv(TAB / "top_hub_results.csv", index=False)

# --- graph summary table ---
summary_rows = [
    ("G_qr nodes", G_qr.number_of_nodes()),
    ("G_qr edges", G_qr.number_of_edges()),
    ("G_qr self-loops", sum(1 for u, v in G_qr.edges() if u == v)),
    ("G_qr unique inputs (out>0)", sum(1 for n, d in G_qr.out_degree() if d > 0)),
    ("G_qr unique rewrites (in>0)", sum(1 for n, d in G_qr.in_degree() if d > 0)),
    ("G_qr density (directed)", nx.density(G_qr)),
    ("G_ql nodes", G_ql.number_of_nodes()),
    ("G_ql edges", G_ql.number_of_edges()),
    ("G_ql unique inputs", sum(1 for n, d in G_ql.out_degree() if d > 0)),
    ("G_ql unique links", sum(1 for n, d in G_ql.in_degree() if d > 0)),
    ("G_ql density (directed)", nx.density(G_ql)),
    ("G_flow nodes", G_flow.number_of_nodes()),
    ("G_flow edges", G_flow.number_of_edges()),
    ("Connected components (Gu)", nx.number_connected_components(Gu)),
    ("Largest CC size (Gu)", max((len(c) for c in nx.connected_components(Gu)), default=0)),
    ("Q-Q rewrite Jaccard (top-10 mean)", None),
]
summary_df = pd.DataFrame(summary_rows, columns=["metric", "value"])
summary_df.to_csv(TAB / "graph_summary.csv", index=False)
print(summary_df.to_string(index=False))

# --- Q-Q rewrite Jaccard (similar rewrites cluster together) ---
# Take all unique inputs and compute jaccard on the set of their downstream rewrites
# But we want a tractable computation: for top-50 most-out-degree nodes, build sets.
top_q_set = {n for n, _ in safe_topk(out_deg, 50)}
neighbors = {n: set(G_qr.successors(n)) for n in top_q_set}
top_jaccard_rows = []
nodes_list = list(top_q_set)
for i, a in enumerate(nodes_list):
    for b in nodes_list[i + 1 :]:
        na, nb = neighbors[a], neighbors[b]
        if not na and not nb:
            continue
        j = len(na & nb) / max(1, len(na | nb))
        top_jaccard_rows.append((a, b, j))
top_jaccard_rows.sort(key=lambda x: x[2], reverse=True)
pd.DataFrame(top_jaccard_rows[:100], columns=["q1", "q2", "jaccard"]).to_csv(
    TAB / "q_q_jaccard_topk.csv", index=False
)
# also a mean Jaccard for the summary
top10 = top_jaccard_rows[:10]
mean_top10 = float(np.mean([j for _, _, j in top10])) if top10 else 0.0
summary_df.loc[summary_df["metric"] == "Q-Q rewrite Jaccard (top-10 mean)", "value"] = round(mean_top10, 4)
summary_df.to_csv(TAB / "graph_summary.csv", index=False)

# --- Community detection (greedy modularity) on Q-R undirected graph ---
# use only nodes with degree >= 2 to avoid noise
nodes_active = [n for n, d in Gu.degree() if d >= 2]
subG = Gu.subgraph(nodes_active).copy()
communities = nx.community.greedy_modularity_communities(subG)
community_assignments = {}
for cid, comm in enumerate(communities):
    for n in comm:
        community_assignments[n] = cid
com_df = (
    pd.DataFrame(
        [(n, c) for n, c in community_assignments.items()],
        columns=["node", "community_id"],
    )
)
com_df["node_type"] = com_df["node"].apply(lambda n: "input" if n in set(pairs["input_key"]) else "rewrite")
com_df.to_csv(TAB / "communities.csv", index=False)
print(f"\nDetected {len(communities)} communities on {len(nodes_active)} active nodes.")
sizes = sorted([len(c) for c in communities], reverse=True)
print("Top 10 community sizes:", sizes[:10])

# --- Q-Q Jaccard heatmap (top 25) ---
top_25 = [n for n, _ in safe_topk(out_deg, 25)]
mat = np.zeros((25, 25))
neigh25 = {n: set(G_qr.successors(n)) for n in top_25}
for i, a in enumerate(top_25):
    for j, b in enumerate(top_25):
        if i == j:
            mat[i, j] = 1.0
            continue
        na, nb = neigh25[a], neigh25[b]
        mat[i, j] = len(na & nb) / max(1, len(na | nb))
fig, ax = plt.subplots(figsize=(10, 9))
im = ax.imshow(mat, cmap="viridis", vmin=0, vmax=1)
ax.set_xticks(range(25))
ax.set_xticklabels([t[:18] for t in top_25], rotation=80, fontsize=7)
ax.set_yticks(range(25))
ax.set_yticklabels([t[:18] for t in top_25], fontsize=7)
ax.set_title("Q-Q Jaccard (rewrite-set overlap) — top 25 hub inputs")
fig.colorbar(im, ax=ax, label="Jaccard")
fig.tight_layout()
fig.savefig(FIG / "jaccard_heatmap_qq.png", dpi=140)
plt.close(fig)

# --- community size distribution ---
fig, ax = plt.subplots(figsize=(8, 4))
ax.hist(sizes, bins=range(1, max(sizes) + 2), edgecolor="black")
ax.set_xlabel("community size")
ax.set_ylabel("# communities")
ax.set_title(f"Greedy modularity communities (n={len(communities)}, mean={np.mean(sizes):.1f})")
fig.tight_layout()
fig.savefig(FIG / "community_size_distribution.png", dpi=140)
plt.close(fig)

# --- Degree centrality: top 20 hub queries & hub results ---
fig, axes = plt.subplots(1, 2, figsize=(14, 6))
top_q20 = safe_topk(out_deg, 20)
axes[0].barh([t[:40] for t, _ in top_q20][::-1], [d for _, d in top_q20][::-1], color="steelblue")
axes[0].set_title("Top 20 input queries by out-degree (number of distinct rewrites)")
axes[0].set_xlabel("out-degree")

top_l20 = safe_topk(indeg_l, 20)
axes[1].barh([l[:60] for l, _ in top_l20][::-1], [d for _, d in top_l20][::-1], color="indianred")
axes[1].set_title("Top 20 final-result URLs by in-degree (appear for most queries)")
axes[1].set_xlabel("in-degree")
fig.tight_layout()
fig.savefig(FIG / "degree_centrality_top_queries.png", dpi=140)
plt.close(fig)

# --- Small subgraph: most-connected 8 inputs + their rewrites (spring layout) ---
top_8 = [n for n, _ in safe_topk(out_deg, 8)]
sub_nodes = set(top_8)
for n in top_8:
    sub_nodes.update(G_qr.successors(n))
subG2 = G_qr.subgraph(sub_nodes)
# Use a colour by role average
role_colours = {
    "original_free": "#888",
    "paid_brave": "#1f77b4",
    "paid_google": "#2ca02c",
    "paid_other": "#ff7f0e",
    "neural": "#d62728",
    "specialized": "#9467bd",
}
edge_colors = []
for u, v, d in subG2.edges(data=True):
    edge_colors.append(role_colours.get(d.get("role"), "#444"))
node_colors = []
for n in subG2.nodes():
    if n in set(pairs["input_key"]):
        node_colors.append("#222")
    else:
        node_colors.append("#ddd")
fig, ax = plt.subplots(figsize=(13, 9))
pos = nx.spring_layout(subG2.to_undirected(), k=0.7, seed=11, iterations=200)
nx.draw_networkx_nodes(subG2, pos, node_size=900, node_color=node_colors, edgecolors="black", ax=ax)
nx.draw_networkx_edges(subG2, pos, edge_color=edge_colors, arrows=True, arrowsize=10, width=0.8, ax=ax)
labels = {n: n[:30] + ("…" if len(n) > 30 else "") for n in subG2.nodes()}
nx.draw_networkx_labels(subG2, pos, labels=labels, font_size=7, ax=ax)
# legend
import matplotlib.patches as mpatches
patches = [mpatches.Patch(color=c, label=r) for r, c in role_colours.items()]
patches += [
    mpatches.Patch(color="#222", label="Input query"),
    mpatches.Patch(color="#ddd", label="Rewrite"),
]
ax.legend(handles=patches, loc="lower left", fontsize=8, frameon=True)
ax.set_axis_off()
ax.set_title("Subgraph: top-8 input queries and their rewrites (colour = branch_role)")
fig.tight_layout()
fig.savefig(FIG / "graph_subgraph_top8.png", dpi=140)
plt.close(fig)

print("\nWrote graph_summary, top_hub_*, communities, figures.")
