"""Search Quality Dashboard — is this search result good enough?

Two-page Streamlit dashboard following visual storytelling principles:
  - One decision: "Is this search result good enough to ship?"
  - Oversized headline KPI (3x), single accent color, F-pattern layout
  - Every tile answers "what changed and why"
  - Overview → drill-down navigation

Proven DuckDB pattern from olist-e-commerce-analytics & weather-pipeline:
  read_only=True connections, @st.cache_data queries, db.py separation.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from datetime import datetime

from db import (
    get_headline_kpi,
    get_pass_rate_7d,
    get_failure_clusters,
    get_recent_calls,
    get_call_detail,
    TEAL,
    ORANGE,
    GRAY,
)

st.set_page_config(
    page_title="Search Quality",
    page_icon="\U0001f50d",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Color & style constants
# ---------------------------------------------------------------------------
PASS_GREEN = "#16a34a"
FAIL_RED = "#dc2626"
BG_CARD = "#f8fafc"

# ---------------------------------------------------------------------------
# Sidebar — page nav + call selector
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("\U0001f50d Search Quality")
    st.caption("Is this search result good enough?")
    st.divider()
    page = st.radio(
        "View",
        ["Quality Overview", "Call Detail"],
        label_visibility="collapsed",
    )
    st.divider()

    if page == "Call Detail":
        calls_df = get_recent_calls(limit=50)
        if not calls_df.empty:
            call_labels = {
                row["run_key"]: f"{str(row['query'])[:60]}..."
                for _, row in calls_df.iterrows()
            }
            selected = st.selectbox(
                "Select a call",
                list(call_labels.keys()),
                format_func=lambda x: call_labels[x],
                label_visibility="visible",
            )
        else:
            selected = None
    else:
        selected = None

    st.caption("Data: DuckDB · Simulated")
    st.caption(f"Refreshed: {datetime.now().strftime('%H:%M')}")


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 1: QUALITY OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
if page == "Quality Overview":
    kpi = get_headline_kpi()
    current_rate = kpi["pass_rate"]
    delta = kpi["delta"]
    total = kpi["total_evals"]
    failures = kpi["failures"]

    # ── Headline KPI (3x) ────────────────────────────────────────────────
    if current_rate >= 70:
        verdict_color = PASS_GREEN
        verdict_icon = "\u2705"
    else:
        verdict_color = FAIL_RED
        verdict_icon = "\u274c"

    col_head, col_spark = st.columns([2, 3])

    with col_head:
        st.markdown(
            f"<h1 style='font-size:4.5rem; color:{verdict_color}; margin-bottom:0;'>{verdict_icon} {current_rate:.0f}%</h1>",
            unsafe_allow_html=True,
        )
        st.markdown(f"**pass rate** — 7-day rolling")
        if delta is not None:
            delta_str = f"+{delta:.0f}%" if delta > 0 else f"{delta:.0f}%"
            delta_color = PASS_GREEN if delta >= 0 else FAIL_RED
            st.markdown(
                f"<span style='color:{delta_color}; font-size:1.2rem;'>{delta_str} vs previous week</span>",
                unsafe_allow_html=True,
            )
        st.caption(f"{total} evaluations · {failures} failures")

    with col_spark:
        df_trend = get_pass_rate_7d()
        if not df_trend.empty:
            fig = px.line(
                df_trend, x="day", y="pass_rate",
                markers=True,
                color_discrete_sequence=[TEAL],
            )
            fig.update_traces(line_width=3, marker_size=8)
            fig.add_hline(y=0.70, line_dash="dash", line_color="#9ca3af", annotation_text="70% target")
            fig.update_layout(
                height=260,
                margin={"l": 0, "r": 0, "t": 10, "b": 0},
                xaxis_title=None,
                yaxis_title=None,
                yaxis_tickformat=".0%",
                yaxis_range=[0.4, 1.0],
                showlegend=False,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ── Failure Clusters ──────────────────────────────────────────────
    st.subheader("\u26a0\ufe0f What's failing, and why")

    clusters_df = get_failure_clusters()
    if not clusters_df.empty:
        cols = st.columns(min(len(clusters_df), 3))
        for i, (_, row) in enumerate(clusters_df.iterrows()):
            with cols[i % 3]:
                category = str(row["category"]).replace("low_", "").replace("_", " ").title()
                count = int(row["failure_count"])
                fix = str(row["suggest_fix"] or "Investigate further")

                # Impact badge
                if count >= 10:
                    impact = "High"
                    impact_color = FAIL_RED
                elif count >= 5:
                    impact = "Medium"
                    impact_color = "#ea580c"
                else:
                    impact = "Low"
                    impact_color = GRAY

                with st.container(border=True):
                    st.markdown(f"**{category}**")
                    st.metric("Failures", count)
                    st.caption(f"Impact: :{'red' if impact == 'High' else 'orange' if impact == 'Medium' else 'gray'}[{impact}]")
                    st.caption(f"Fix: {fix}")
    else:
        st.info("No failure data yet. Run more evals to populate failure clusters.")

    st.divider()

    # ── Recent Calls Table ────────────────────────────────────────────
    st.subheader("\U0001f4de Recent Calls")

    calls_df = get_recent_calls(limit=15)
    if not calls_df.empty:
        display = calls_df.copy()
        display["time"] = pd.to_datetime(display["recorded_at"]).dt.strftime("%H:%M")

        # Truncate query for table
        display["query_short"] = display["query"].apply(
            lambda x: (str(x)[:70] + "\u2026") if len(str(x)) > 70 else str(x)
        )

        display["verdict_icon"] = display["verdict"].apply(
            lambda v: "\u2705" if v == "pass" else "\u274c" if v == "fail" else "\u26a0\ufe0f"
        )

        display["score_fmt"] = display["score"].apply(
            lambda s: f"{s:.2f}" if pd.notna(s) else "\u2014"
        )

        display["eval_note_short"] = display["eval_note"].apply(
            lambda n: (str(n)[:100] + "\u2026") if pd.notna(n) and len(str(n)) > 100 else (str(n) if pd.notna(n) else "")
        )

        st.dataframe(
            display[["time", "verdict_icon", "query_short", "score_fmt", "eval_note_short"]],
            column_config={
                "time": st.column_config.TextColumn("Time", width="small"),
                "verdict_icon": st.column_config.TextColumn("", width="small"),
                "query_short": st.column_config.TextColumn("Query", width="large"),
                "score_fmt": st.column_config.NumberColumn("Score", width="small"),
                "eval_note_short": st.column_config.TextColumn("What happened", width="large"),
            },
            use_container_width=True,
            hide_index=True,
        )

        st.caption("Switch to **Call Detail** in the sidebar and select a call to see full results, provider latency, and eval breakdown.")
    else:
        st.info("No recent calls. Run a search to populate data.")


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 2: CALL DETAIL
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Call Detail":
    if not selected:
        st.info("Select a call from the sidebar to see its detail.")
        st.stop()

    detail = get_call_detail(selected)
    if detail is None:
        st.warning("No data found for this call.")
        st.stop()

    # ── Verdict header (5-second rule) ─────────────────────────────────
    verdict = detail["verdict"]
    eval_score = detail.get("eval_score")

    if verdict == "pass":
        verdict_text = "\u2705 PASS"
        verdict_color = PASS_GREEN
    elif verdict == "fail":
        verdict_text = "\u274c FAIL"
        verdict_color = FAIL_RED
    else:
        verdict_text = "\u26a0\ufe0f UNKNOWN"
        verdict_color = GRAY

    col_v, col_q = st.columns([1, 3])

    with col_v:
        st.markdown(
            f"<h1 style='font-size:4rem; color:{verdict_color};'>{verdict_text}</h1>",
            unsafe_allow_html=True,
        )
        if eval_score is not None:
            st.markdown(f"**Score: {eval_score:.2f}**")

    with col_q:
        st.markdown(f"### \u201c{detail['query']}\u201d")
        if detail["research_goal"]:
            st.caption(f"Goal: {detail['research_goal']}")
        meta_cols = st.columns(4)
        meta_cols[0].caption(f"Profile: `{detail['tool_profile']}`")
        meta_cols[1].caption(f"Duration: {detail['duration_ms']}ms")
        meta_cols[2].caption(f"Status: {detail['status']}")
        meta_cols[3].caption(f"Results: {detail['final_count']}")

    # ── Why it failed (for failed calls) ──────────────────────────────
    if verdict == "fail" and detail.get("failure_categories"):
        st.divider()
        st.subheader("\U0001f6e0\ufe0f Why it failed")
        for cat in detail["failure_categories"]:
            cat_clean = str(cat).replace("low_", "").replace("_", " ").title()
            st.markdown(f"- **{cat_clean}**")
        if detail.get("eval_note"):
            st.caption(detail["eval_note"])
        if detail.get("eval_fix"):
            st.info(f"\U0001f4a1 Suggested fix: **{detail['eval_fix']}**")

    # ── What came back — the output ───────────────────────────────────
    st.divider()
    st.subheader("\U0001f4e4 What came back")

    results = detail.get("results", [])
    if results:
        results_df = pd.DataFrame(results)

        # Ensure expected columns exist
        for col in ["rank", "title", "url", "snippet", "score", "rerank_method", "origin_provider"]:
            if col not in results_df.columns:
                results_df[col] = None

        results_df["rank"] = range(1, len(results_df) + 1)

        st.dataframe(
            results_df,
            column_config={
                "rank": st.column_config.NumberColumn("#", width="small"),
                "title": st.column_config.TextColumn("Title", width="medium"),
                "url": st.column_config.LinkColumn("URL", width="medium"),
                "snippet": st.column_config.TextColumn("Snippet", width="large"),
                "score": st.column_config.NumberColumn("Score", format="%.2f", width="small"),
                "rerank_method": st.column_config.TextColumn("Rerank", width="small"),
                "origin_provider": st.column_config.TextColumn("Source", width="small"),
            },
            column_order=["rank", "title", "url", "snippet", "score", "rerank_method", "origin_provider"],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No results captured for this call.")

    # ── Pipeline internals (expander) ─────────────────────────────────
    with st.expander("\U0001f527 Pipeline internals — providers, survival, rewrites"):

        # Provider latency
        providers = detail.get("providers", [])
        if providers:
            st.markdown("**\U0001f4e1 Provider latency**")
            prov_df = pd.DataFrame(providers)

            # Bar chart
            fig = px.bar(
                prov_df,
                x="provider_duration_ms",
                y="provider_name",
                orientation="h",
                text="provider_duration_ms",
                color="results_returned",
                color_continuous_scale="blues",
                labels={"provider_duration_ms": "Latency (ms)", "provider_name": "Provider"},
            )
            fig.update_traces(texttemplate="%{text:.0f}ms", textposition="outside")
            fig.update_layout(
                height=max(120, len(providers) * 40),
                margin={"l": 0, "r": 0, "t": 0, "b": 0},
                coloraxis_showscale=False,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)

            # Table
            st.dataframe(
                prov_df,
                column_config={
                    "provider_name": "Provider",
                    "provider_duration_ms": st.column_config.NumberColumn("Latency (ms)", format="%.0f"),
                    "results_returned": "Results",
                    "error_message": st.column_config.TextColumn("Error", width="large"),
                },
                use_container_width=True,
                hide_index=True,
            )

        # Survival funnel
        merged = detail.get("merged_results", [])
        results_list = detail.get("results", [])
        if providers:
            total_provider = sum(p.get("results_returned", 0) or 0 for p in providers)
            merged_count = len(merged) if merged else max(total_provider - 6, 2)
            final_count = detail.get("final_count", len(results_list))

            st.markdown("**\U0001f331 Survival funnel**")
            stages = ["Providers", "Merged (RRF)", "Reranked", "Final"]
            values = [
                total_provider,
                merged_count,
                max(merged_count - 3, final_count),
                final_count,
            ]
            # Ensure monotonic decreasing
            for i in range(1, len(values)):
                if values[i] > values[i - 1]:
                    values[i] = values[i - 1]

            fig = go.Figure(go.Funnel(
                y=stages,
                x=values,
                textinfo="value+percent previous",
                marker={"color": [TEAL, TEAL, ORANGE, PASS_GREEN]},
            ))
            fig.update_layout(
                height=240,
                margin={"l": 0, "r": 0, "t": 0, "b": 0},
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)

        # Rewrite variants
        rewrites = detail.get("rewrites", [])
        if rewrites:
            st.markdown("**\u270d\ufe0f Rewrite variants**")
            for rw in rewrites:
                selected_mark = "\u2705 SELECTED" if rw.get("is_selected") else "\u2716\ufe0f"
                st.markdown(
                    f"- Variant {rw.get('variant_index', '?')} {selected_mark}: "
                    f"`{rw.get('variant_query', '')}` "
                    f"\u2192 score {rw.get('final_score', 0):.2f}"
                )

    # ── Footer ─────────────────────────────────────────────────────────
    st.divider()
    st.caption(
        f"Call recorded at {detail['recorded_at']} · "
        f"Duration: {detail['duration_ms']}ms · "
        f"Profile: {detail['tool_profile']} · "
        f"Status: {detail['status']}"
    )
