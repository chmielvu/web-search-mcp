#!/usr/bin/env python3
"""Query transformation analysis: input vs rewritten branch queries.

For each (run, branch_role) pair, measure how the rewrite differs from the
input. Output several summary tables and a plot.

Outputs (tables/):
  - branch_lengths.csv         (per-pair char/word/token counts)
  - branch_similarity.csv      (Jaccard, containment, length ratio, Jaro-Winkler)
  - branch_diff_stats.csv      (per-branch_role aggregate)
  - branch_intent_drift.csv    (cluster alignment, see 03_)

Figures (figures/):
  - branch_length_distribution.png
  - branch_length_ratio_distribution.png
  - branch_token_jaccard_distribution.png
  - branch_similarity_heatmap.png
  - branch_term_topadd_topdrop.png
"""
from __future__ import annotations

import re
import string
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

pairs = pd.read_parquet(ART / "run_branch_pairs.parquet")

# ---------------- helpers ----------------
WORD = re.compile(r"[A-Za-z0-9]+")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-\.]*")


def normalize(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return " ".join(s.split()).strip()


def tokens_loose(s: str) -> set[str]:
    return {t.lower() for t in TOKEN.findall(s or "")}


def tokens_alpha(s: str) -> set[str]:
    return {t.lower() for t in WORD.findall(s or "")}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(a: set[str], b: set[str]) -> float:
    if not b:
        return 0.0
    return len(a & b) / len(b)


def jaro_winkler(s1: str, s2: str) -> float:
    # simple implementation
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    len1, len2 = len(s1), len(s2)
    match_dist = max(len1, len2) // 2 - 1
    if match_dist < 0:
        match_dist = 0
    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    for i, ch in enumerate(s1):
        start = max(0, i - match_dist)
        end = min(i + match_dist + 1, len2)
        for j in range(start, end):
            if s2_matches[j]:
                continue
            if s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0
    t = 0
    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            t += 1
        k += 1
    t //= 2
    m = matches
    jaro = (m / len1 + m / len2 + (m - t) / m) / 3
    # Winkler bonus
    prefix = 0
    for i in range(min(4, len1, len2)):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break
    return jaro + prefix * 0.1 * (1 - jaro)


# ---------------- enrich pairs ----------------
pairs["input_norm"] = pairs["input_query"].map(normalize)
pairs["branch_norm"] = pairs["branch_query"].map(normalize)
pairs["input_tokens"] = pairs["input_norm"].map(lambda s: tokens_loose(s))
pairs["branch_tokens"] = pairs["branch_norm"].map(lambda s: tokens_loose(s))
pairs["input_words"] = pairs["input_norm"].map(lambda s: tokens_alpha(s))
pairs["branch_words"] = pairs["branch_norm"].map(lambda s: tokens_alpha(s))
pairs["input_chars"] = pairs["input_norm"].str.len()
pairs["branch_chars"] = pairs["branch_norm"].str.len()
pairs["input_word_count"] = pairs["input_words"].map(len)
pairs["branch_word_count"] = pairs["branch_words"].map(len)
pairs["input_token_count"] = pairs["input_tokens"].map(len)
pairs["branch_token_count"] = pairs["branch_tokens"].map(len)
pairs["char_ratio"] = pairs["branch_chars"] / pairs["input_chars"].clip(lower=1)
pairs["word_ratio"] = pairs["branch_word_count"] / pairs["input_word_count"].clip(lower=1)
pairs["jaccard"] = [jaccard(a, b) for a, b in zip(pairs["input_tokens"], pairs["branch_tokens"])]
pairs["containment_input_in_branch"] = [
    containment(b, a) for a, b in zip(pairs["input_tokens"], pairs["branch_tokens"])
]
pairs["containment_branch_in_input"] = [
    containment(a, b) for a, b in zip(pairs["input_tokens"], pairs["branch_tokens"])
]
pairs["identical"] = pairs["input_norm"] == pairs["branch_norm"]
# Sample 4000 jaro-winkler to keep speed reasonable
sample = pairs.sample(min(4000, len(pairs)), random_state=42)
jw = np.array(
    [jaro_winkler(a, b) for a, b in zip(sample["input_norm"], sample["branch_norm"])]
)
sample = sample.assign(jaro_winkler=jw)
sample[["run_key", "branch_role", "jaro_winkler"]].to_csv(
    TAB / "branch_jaro_sample.csv", index=False
)

# ---------------- per-pair length table ----------------
lengths = pairs[
    [
        "run_key",
        "branch_role",
        "intent",
        "input_chars",
        "branch_chars",
        "char_ratio",
        "input_word_count",
        "branch_word_count",
        "word_ratio",
        "input_token_count",
        "branch_token_count",
        "identical",
        "jaccard",
    ]
]
lengths.to_csv(TAB / "branch_lengths.csv", index=False)

# ---------------- aggregate per role ----------------
agg = (
    pairs.groupby("branch_role")
    .agg(
        n=("run_key", "size"),
        identical_pct=("identical", "mean"),
        char_ratio_median=("char_ratio", "median"),
        char_ratio_p25=("char_ratio", lambda s: float(np.nanpercentile(s, 25))),
        char_ratio_p75=("char_ratio", lambda s: float(np.nanpercentile(s, 75))),
        word_ratio_median=("word_ratio", "median"),
        jaccard_median=("jaccard", "median"),
        jaccard_p25=("jaccard", lambda s: float(np.nanpercentile(s, 25))),
        jaccard_p75=("jaccard", lambda s: float(np.nanpercentile(s, 75))),
        containment_in_branch_median=("containment_input_in_branch", "median"),
        containment_in_input_median=("containment_branch_in_input", "median"),
    )
    .reset_index()
)
agg.to_csv(TAB / "branch_diff_stats.csv", index=False)
print("Per-role diff stats:")
print(agg.to_string(index=False))

# ---------------- term-level addition/removal per role ----------------
def term_diff_stats(role: str, top: int = 15) -> dict:
    sub = pairs[pairs["branch_role"] == role]
    added_counter: Counter[str] = Counter()
    removed_counter: Counter[str] = Counter()
    for a, b in zip(sub["input_tokens"], sub["branch_tokens"]):
        added_counter.update(b - a)
        removed_counter.update(a - b)
    return {
        "added": added_counter.most_common(top),
        "removed": removed_counter.most_common(top),
    }


diff_summary = {role: term_diff_stats(role) for role in pairs["branch_role"].unique()}
with open(TAB / "branch_term_changes.txt", "w", encoding="utf-8") as f:
    for role, data in diff_summary.items():
        f.write(f"### {role}\n")
        f.write("Top added tokens (branch \\ input):\n")
        for tok, c in data["added"]:
            f.write(f"  {tok:>20s}  +{c}\n")
        f.write("Top removed tokens (input \\ branch):\n")
        for tok, c in data["removed"]:
            f.write(f"  {tok:>20s}  -{c}\n")
        f.write("\n")
print("\nWrote branch_term_changes.txt")

# ---------------- structural operator patterns ----------------
def detect_ops(s: str) -> dict:
    return {
        "has_site": int(bool(re.search(r"\bsite:[^\s]+", s or ""))),
        "has_quoted": int('"' in (s or "")),
        "has_plus": int(bool(re.search(r"(^|\s)\+", s or ""))),
        "has_minus": int(bool(re.search(r"(^|\s)-", s or ""))),
        "has_or": int(bool(re.search(r"\bOR\b", s or "", re.I))),
        "has_filetype": int(bool(re.search(r"\bfiletype:", s or "", re.I))),
        "has_intitle": int(bool(re.search(r"\bintitle:", s or "", re.I))),
        "has_inurl": int(bool(re.search(r"\binurl:", s or "", re.I))),
        "n_plus": len(re.findall(r"(^|\s)\+\S+", s or "")),
        "n_minus": len(re.findall(r"(^|\s)-\S+", s or "")),
        "n_or": len(re.findall(r"\bOR\b", s or "", re.I)),
        "n_quoted": (s or "").count('"') // 2,
    }


op_rows = []
for role in pairs["branch_role"].unique():
    sub = pairs[pairs["branch_role"] == role]
    in_ops = pd.DataFrame([detect_ops(s) for s in sub["input_norm"]]).mean()
    br_ops = pd.DataFrame([detect_ops(s) for s in sub["branch_norm"]]).mean()
    for k in in_ops.index:
        op_rows.append(
            {
                "branch_role": role,
                "operator": k,
                "input_freq": float(in_ops[k]),
                "branch_freq": float(br_ops[k]),
                "delta": float(br_ops[k] - in_ops[k]),
            }
        )
op_df = pd.DataFrame(op_rows)
op_df.to_csv(TAB / "branch_operator_usage.csv", index=False)
print("\nOperator usage by role (delta = branch - input):")
print(op_df.pivot(index="operator", columns="branch_role", values="delta").round(3).to_string())

# ---------------- figures ----------------
plt.style.use("seaborn-v0_8-whitegrid")

# 1. Length distribution per role
fig, ax = plt.subplots(figsize=(10, 6))
roles = sorted(pairs["branch_role"].unique())
data = [pairs.loc[pairs["branch_role"] == r, "branch_word_count"].values for r in roles]
bp = ax.boxplot(data, tick_labels=roles, showfliers=False, patch_artist=True)
colors = plt.cm.tab10(np.linspace(0, 1, len(roles)))
for patch, c in zip(bp["boxes"], colors):
    patch.set_facecolor(c)
    patch.set_alpha(0.6)
ax.set_ylabel("Branch query word count")
ax.set_title("Branch query length distribution per role")
plt.xticks(rotation=20, ha="right")
fig.tight_layout()
fig.savefig(FIG / "branch_length_distribution.png", dpi=140)
plt.close(fig)

# 2. Char ratio distribution per role
fig, ax = plt.subplots(figsize=(10, 6))
data = [pairs.loc[pairs["branch_role"] == r, "char_ratio"].values for r in roles]
bp = ax.boxplot(data, tick_labels=roles, showfliers=False, patch_artist=True)
for patch, c in zip(bp["boxes"], colors):
    patch.set_facecolor(c)
    patch.set_alpha(0.6)
ax.axhline(1.0, color="grey", linestyle="--", linewidth=1, label="ratio = 1.0")
ax.set_ylabel("char(branch) / char(input)")
ax.set_title("Length ratio distribution per role")
ax.legend(loc="upper right")
plt.xticks(rotation=20, ha="right")
fig.tight_layout()
fig.savefig(FIG / "branch_length_ratio_distribution.png", dpi=140)
plt.close(fig)

# 3. Jaccard distribution per role
fig, ax = plt.subplots(figsize=(10, 6))
data = [pairs.loc[pairs["branch_role"] == r, "jaccard"].values for r in roles]
bp = ax.boxplot(data, tick_labels=roles, showfliers=False, patch_artist=True)
for patch, c in zip(bp["boxes"], colors):
    patch.set_facecolor(c)
    patch.set_alpha(0.6)
ax.set_ylabel("Token Jaccard (input vs branch)")
ax.set_title("Rewrite divergence per role")
plt.xticks(rotation=20, ha="right")
fig.tight_layout()
fig.savefig(FIG / "branch_token_jaccard_distribution.png", dpi=140)
plt.close(fig)

# 4. Similarity heatmap (median per metric, per role)
heat = agg.set_index("branch_role")[
    [
        "identical_pct",
        "jaccard_median",
        "containment_in_branch_median",
        "containment_in_input_median",
        "word_ratio_median",
    ]
]
fig, ax = plt.subplots(figsize=(8, 5))
im = ax.imshow(heat.values, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
ax.set_xticks(range(heat.shape[1]))
ax.set_xticklabels(heat.columns, rotation=20, ha="right")
ax.set_yticks(range(heat.shape[0]))
ax.set_yticklabels(heat.index)
for i in range(heat.shape[0]):
    for j in range(heat.shape[1]):
        ax.text(j, i, f"{heat.values[i, j]:.2f}", ha="center", va="center", fontsize=9)
fig.colorbar(im, ax=ax, label="value (0-1)")
ax.set_title("Median per-role similarity / ratio")
fig.tight_layout()
fig.savefig(FIG / "branch_similarity_heatmap.png", dpi=140)
plt.close(fig)

# 5. Top added / dropped per role
fig, axes = plt.subplots(1, 2, figsize=(14, 6))
top_added_rows = []
for role, d in diff_summary.items():
    for tok, c in d["added"][:8]:
        top_added_rows.append({"role": role, "token": tok, "count": c})
df_add = pd.DataFrame(top_added_rows)
pivot_add = df_add.pivot(index="role", columns="token", values="count").fillna(0)
sns_data = pivot_add.values
im = axes[0].imshow(np.log1p(sns_data), cmap="YlOrRd", aspect="auto")
axes[0].set_xticks(range(pivot_add.shape[1]))
axes[0].set_xticklabels(pivot_add.columns, rotation=45, ha="right", fontsize=8)
axes[0].set_yticks(range(pivot_add.shape[0]))
axes[0].set_yticklabels(pivot_add.index)
axes[0].set_title("Top added tokens (log1p)")

top_removed_rows = []
for role, d in diff_summary.items():
    for tok, c in d["removed"][:8]:
        top_removed_rows.append({"role": role, "token": tok, "count": c})
df_rm = pd.DataFrame(top_removed_rows)
pivot_rm = df_rm.pivot(index="role", columns="token", values="count").fillna(0)
im = axes[1].imshow(np.log1p(pivot_rm.values), cmap="YlOrRd", aspect="auto")
axes[1].set_xticks(range(pivot_rm.shape[1]))
axes[1].set_xticklabels(pivot_rm.columns, rotation=45, ha="right", fontsize=8)
axes[1].set_yticks(range(pivot_rm.shape[0]))
axes[1].set_yticklabels(pivot_rm.index)
axes[1].set_title("Top removed tokens (log1p)")

fig.tight_layout()
fig.savefig(FIG / "branch_term_topadd_topdrop.png", dpi=140)
plt.close(fig)

print("\nAll query transformation figures written to figures/")
print("All query transformation tables written to tables/")
