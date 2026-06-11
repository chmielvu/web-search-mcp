import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import json

st.set_page_config(page_title="Search Quality Dashboard", layout="wide", page_icon="🔍")

# ─── MOCK DATA ───────────────────────────────────────────────────────────────

SEARCH_CALLS = [
    {
        "call_id": "call_a1b2c3",
        "started_at": datetime(2026, 6, 7, 14, 23, 12),
        "duration_ms": 2340,
        "status": "ok",
        "tool_profile": "research",
        "query": "best python web framework 2026",
        "research_goal": "Compare FastAPI, Django, Flask for a new production project",
        "num_results": 10,
        "rewrite_enabled": True,
        "result_offset": 0,
        "providers_requested": ["searxng", "tavily", "brave"],
        "searxng_language": "en",
        "searxng_engines": None,
        "domain_boost": ["github.com", "stackoverflow.com"],
        "domain_block": ["pinterest.com", "quora.com"],
        "site_filters": None,
        "final_count": 8,
        "error_class": None,
        "error_message": None,
        "caller_session_id": "session_xyz",
        "trace_id": "trace_789",
        "evaluated": True,
    },
    {
        "call_id": "call_d4e5f6",
        "started_at": datetime(2026, 6, 7, 14, 25, 45),
        "duration_ms": 4120,
        "status": "ok",
        "tool_profile": "default",
        "query": "FastAPI async middleware TypeError: 'NoneType' object is not callable",
        "research_goal": "Debug a FastAPI middleware error in production",
        "num_results": 5,
        "rewrite_enabled": True,
        "result_offset": 0,
        "providers_requested": ["searxng", "tavily"],
        "searxng_language": "en",
        "searxng_engines": None,
        "domain_boost": ["stackoverflow.com", "github.com"],
        "domain_block": None,
        "site_filters": None,
        "final_count": 5,
        "error_class": None,
        "error_message": None,
        "caller_session_id": "session_xyz",
        "trace_id": "trace_790",
        "evaluated": True,
    },
    {
        "call_id": "call_g7h8i9",
        "started_at": datetime(2026, 6, 7, 14, 30, 18),
        "duration_ms": 5890,
        "status": "partial",
        "tool_profile": "research",
        "query": "Rust vs Go for microservices 2026 production experience",
        "research_goal": "Decision analysis: which language for new microservices infrastructure",
        "num_results": 10,
        "rewrite_enabled": True,
        "result_offset": 0,
        "providers_requested": ["searxng", "tavily", "brave", "jina"],
        "searxng_language": "en",
        "searxng_engines": None,
        "domain_boost": ["reddit.com", "news.ycombinator.com", "stackoverflow.com"],
        "domain_block": ["medium.com", "dev.to"],
        "site_filters": None,
        "final_count": 6,
        "error_class": "TimeoutError",
        "error_message": "Jina provider timed out after 5000ms",
        "caller_session_id": "session_abc",
        "trace_id": "trace_791",
        "evaluated": True,
    },
]

PROVIDER_RESULTS = {
    "call_a1b2c3": [
        {"provider": "searxng", "duration_ms": 890, "results_returned": 12, "error_message": None},
        {"provider": "tavily", "duration_ms": 1200, "results_returned": 8, "error_message": None},
        {"provider": "brave", "duration_ms": 1500, "results_returned": 6, "error_message": None},
    ],
    "call_d4e5f6": [
        {"provider": "searxng", "duration_ms": 1100, "results_returned": 8, "error_message": None},
        {"provider": "tavily", "duration_ms": 2900, "results_returned": 5, "error_message": None},
    ],
    "call_g7h8i9": [
        {"provider": "searxng", "duration_ms": 950, "results_returned": 10, "error_message": None},
        {"provider": "tavily", "duration_ms": 1800, "results_returned": 7, "error_message": None},
        {"provider": "brave", "duration_ms": 2100, "results_returned": 5, "error_message": None},
        {"provider": "jina", "duration_ms": 5000, "results_returned": 0, "error_message": "TimeoutError: Timed out after 5000ms"},
    ],
}

FINAL_RESULTS = {
    "call_a1b2c3": [
        {"rank": 1, "url": "https://github.com/fastapi/fastapi", "title": "FastAPI repo", "snippet": "FastAPI is a modern, fast web framework for building APIs with Python 3.8+ based on standard Python type hints. Very high performance, on par with NodeJS and Go.", "score": 0.94, "rerank_method": "bi_encoder", "origin_provider": "searxng"},
        {"rank": 2, "url": "https://docs.djangoproject.com/en/stable/", "title": "Django docs", "snippet": "Django is a high-level Python web framework that encourages rapid development and clean, pragmatic design. It takes care of much of the hassle of web development.", "score": 0.88, "rerank_method": "bi_encoder", "origin_provider": "tavily"},
        {"rank": 3, "url": "https://flask.palletsprojects.com/en/stable/", "title": "Flask docs", "snippet": "Flask is a lightweight WSGI web application framework. It is designed to make getting started quick and easy, with the ability to scale up to complex applications.", "score": 0.82, "rerank_method": "provider", "origin_provider": "brave"},
        {"rank": 4, "url": "https://realpython.com/fastapi-vs-django-vs-flask/", "title": "Real Python comparison", "snippet": "Comprehensive comparison: FastAPI leads for API-first projects, Django excels for full-stack, Flask is best for microservices and minimal overhead.", "score": 0.76, "rerank_method": "provider", "origin_provider": "tavily"},
        {"rank": 5, "url": "https://stackoverflow.com/questions/78901234/fastapi-vs-django-rest-framework", "title": "SO: FastAPI vs DRF", "snippet": "FastAPI provides automatic OpenAPI docs, built-in validation with Pydantic, and async support. DRF has more mature ecosystem and better third-party package support.", "score": 0.72, "rerank_method": "diversity", "origin_provider": "brave"},
        {"rank": 6, "url": "https://news.ycombinator.com/item?id=34567890", "title": "HN: FastAPI in Production", "snippet": "Discussion about production experiences with FastAPI. Users report success for API services but note Django remains stronger for full-stack applications.", "score": 0.65, "rerank_method": "diversity", "origin_provider": "tavily"},
    ],
    "call_d4e5f6": [
        {"rank": 1, "url": "https://stackoverflow.com/questions/73487634/fastapi-middleware-typeerror-nonetype-object-is-not-callable", "title": "SO: FastAPI middleware TypeError", "snippet": "This error occurs when you register a middleware without the correct callable signature. Make sure your middleware function accepts `request` and `call_next` parameters.", "score": 0.92, "rerank_method": "bi_encoder", "origin_provider": "searxng"},
        {"rank": 2, "url": "https://fastapi.tiangolo.com/tutorial/middleware/", "title": "FastAPI middleware docs", "snippet": "A middleware is a function that works with every request before it is processed by any specific path operation, and also with every response before returning it.", "score": 0.88, "rerank_method": "bi_encoder", "origin_provider": "tavily"},
        {"rank": 3, "url": "https://github.com/fastapi/fastapi/issues/4567", "title": "GitHub: middleware NoneType issue", "snippet": "Solution: ensure the middleware function returns `await call_next(request)` and that the middleware registration is done before defining routes.", "score": 0.81, "rerank_method": "provider", "origin_provider": "searxng"},
        {"rank": 4, "url": "https://stackoverflow.com/questions/89012345/fastapi-middleware-order-issues", "title": "SO: middleware execution order", "snippet": "FastAPI middleware execution follows registration order. CORSMiddleware should be first. Auth middleware runs before body parsing middleware.", "score": 0.73, "rerank_method": "diversity", "origin_provider": "tavily"},
    ],
    "call_g7h8i9": [
        {"rank": 1, "url": "https://news.ycombinator.com/item?id=33445566", "title": "HN: Rust vs Go discussion", "snippet": "Go wins on dev speed, deployment simplicity, goroutine concurrency. Rust wins on performance, memory safety, compile-time guarantees. Both excellent depending on team.", "score": 0.88, "rerank_method": "bi_encoder", "origin_provider": "searxng"},
        {"rank": 2, "url": "https://stackoverflow.com/questions/56789012/rust-vs-go-microservices-2026", "title": "SO: Rust vs Go in 2026", "snippet": "Go offers faster dev cycles, excellent stdlib for HTTP/gRPC, simpler concurrency. Rust offers better perf, guaranteed memory safety, zero-cost abstractions.", "score": 0.84, "rerank_method": "bi_encoder", "origin_provider": "tavily"},
        {"rank": 3, "url": "https://reddit.com/r/rust/comments/abcdef/rust_vs_go_production_experience/", "title": "r/rust: production experience", "snippet": "Migrated from Go to Rust for API gateway: 40% less resources but 60% more dev time. Worth it for perf-critical paths. Go stays for simple CRUD services.", "score": 0.78, "rerank_method": "provider", "origin_provider": "brave"},
        {"rank": 4, "url": "https://reddit.com/r/golang/comments/ghijkl/why_we_chose_go_over_rust/", "title": "r/golang: why we chose Go", "snippet": "Three-month eval chose Go: faster onboarding, excellent tooling, simpler debugging, goroutines sufficient for workloads. Rust reserved for perf-critical networking.", "score": 0.74, "rerank_method": "provider", "origin_provider": "tavily"},
        {"rank": 5, "url": "https://blog.rust-lang.org/2026/01/rust-in-production-survey.html", "title": "Rust Production Survey", "snippet": "78% say Rust met/exceeded perf expectations. 65% say dev time longer. Top uses: API gateways, auth services, data pipelines, embedded.", "score": 0.69, "rerank_method": "diversity", "origin_provider": "brave"},
    ],
}

EVAL_RESULTS = {
    "call_a1b2c3": {
        "passed": True,
        "pass_rate_score": 0.82,
        "faithfulness": 0.78,
        "groundedness": 0.85,
        "context_precision": 0.72,
        "context_recall": 0.79,
        "answer_relevance": 0.88,
        "mrr_at_10": 0.92,
        "ndcg_at_10": 0.81,
        "failure_categories": ["low_context_precision"],
        "human_readable": "Good coverage of major Python web frameworks. Results include FastAPI, Django, and Flask from authoritative sources. Some irrelevant results (video tutorial) in lower ranks. Context precision could be improved by filtering out video content for comparison queries.",
    },
    "call_d4e5f6": {
        "passed": True,
        "pass_rate_score": 0.91,
        "faithfulness": 0.95,
        "groundedness": 0.92,
        "context_precision": 0.88,
        "context_recall": 0.85,
        "answer_relevance": 0.94,
        "mrr_at_10": 1.0,
        "ndcg_at_10": 0.93,
        "failure_categories": [],
        "human_readable": "Excellent results. Top result is the exact StackOverflow answer for the specific TypeError. Second result is the official FastAPI middleware docs. Both highly relevant and authoritative. The user can immediately fix their bug.",
    },
    "call_g7h8i9": {
        "passed": False,
        "pass_rate_score": 0.61,
        "faithfulness": 0.72,
        "groundedness": 0.68,
        "context_precision": 0.55,
        "context_recall": 0.62,
        "answer_relevance": 0.59,
        "mrr_at_10": 0.78,
        "ndcg_at_10": 0.65,
        "failure_categories": ["low_context_precision", "low_answer_relevance", "partial_coverage"],
        "human_readable": "Partial results due to Jina timeout. Missing Reddit discussion threads and benchmark data that would have been available via Jina. Results are opinion-based (HN discussions) rather than data-driven. Missing factual performance benchmarks and official language documentation. Consider retrying with only searxng and tavily.",
    },
}

REWRITE_VARIANTS = {
    "call_a1b2c3": [
        {"variant_index": 0, "variant_query": "best python web framework 2026", "intent": "informational", "policy": "balanced", "is_selected": True, "results_count": 8, "final_score": 0.82},
        {"variant_index": 1, "variant_query": "python web framework comparison 2026 FastAPI Django Flask", "intent": "informational", "policy": "precision", "is_selected": False, "results_count": 12, "final_score": 0.88},
        {"variant_index": 2, "variant_query": "best python framework for building APIs production 2026", "intent": "transactional", "policy": "balanced", "is_selected": False, "results_count": 7, "final_score": 0.76},
    ],
    "call_d4e5f6": [
        {"variant_index": 0, "variant_query": "FastAPI async middleware TypeError: 'NoneType' object is not callable", "intent": "navigational", "policy": "precision", "is_selected": True, "results_count": 5, "final_score": 0.91},
        {"variant_index": 1, "variant_query": "FastAPI middleware NoneType not callable error fix", "intent": "informational", "policy": "precision", "is_selected": False, "results_count": 8, "final_score": 0.86},
    ],
    "call_g7h8i9": [
        {"variant_index": 0, "variant_query": "Rust vs Go for microservices 2026 production experience", "intent": "informational", "policy": "balanced", "is_selected": True, "results_count": 6, "final_score": 0.61},
        {"variant_index": 1, "variant_query": "Rust Go microservices comparison production 2026 benchmarks", "intent": "informational", "policy": "recall", "is_selected": False, "results_count": 3, "final_score": 0.55},
    ],
}

# ─── PAGE 1: QUALITY OVERVIEW ─────────────────────────────────────────────────

def render_overview():
    st.header("📊 Search Quality Overview")
    st.caption("How good are our search results, and what should we fix?")

    metrics = [
        ("Overall Pass Rate", "73%", "↑ 5% from last week", "✅"),
        ("Calls Evaluated", "1,247", "last 24h", "📞"),
        ("Avg Time to First Result", "1.2s", "p95: 3.8s", "⏱"),
        ("Failure Clusters Active", "3", "precision, coverage, relevance", "⚠️"),
    ]
    for label, value, subtitle, icon in metrics:
        st.markdown(f"### {icon} {label}")
        st.markdown(f"**{value}**")
        st.caption(subtitle)
        st.divider()

    st.subheader("Pass Rate Over Time (7 days)")
    pass_rate_stub = {"Mon": 0.71, "Tue": 0.68, "Wed": 0.74, "Thu": 0.79, "Fri": 0.73, "Sat": 0.70, "Sun": 0.73}
    df = pd.DataFrame({"day": list(pass_rate_stub.keys()), "pass_rate": list(pass_rate_stub.values())})
    fig = px.line(df, x="day", y="pass_rate", markers=True, range_y=[0.5, 1.0])
    fig.update_layout(height=250, margin=dict(l=0, r=0, t=0, b=0), showlegend=False)
    fig.add_hline(y=0.70, line_dash="dash", line_color="red", annotation_text="target")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Top Failure Clusters")
    clusters = [
        {"cluster": "Low Context Precision", "count": 34, "impact": "High",
         "example": '"What is the latest React version?"',
         "fix": "Add domain filters for official docs",
         },
        {"cluster": "Low Faithfulness", "count": 28, "impact": "High",
         "example": '"Compare TypeScript vs JavaScript"',
         "fix": "Improve snippet extraction quality",
         },
        {"cluster": "Partial Coverage (Provider Timeout)", "count": 19, "impact": "Medium",
         "example": '"Rust vs Go for microservices"',
         "fix": "Add fallback provider for timeout scenarios",
         },
    ]
    for c in clusters:
        with st.container(border=1):
            col1, col2 = st.columns([4, 1])
            col1.markdown(f"**{c['cluster']}** — {c['count']} failures")
            col2.markdown(f"Impact: **{c['impact']}**")
            st.caption(f"Example: `{c['example']}`")
            st.markdown(f"Suggested fix: *{c['fix']}*")

    st.subheader("Recent Calls")
    overview_data = [
        {"call_id": "call_g7h8i9", "time": "14:30", "query": "Rust vs Go for microservices 2026...", "pass": "❌", "score": 0.61, "note": "Jina timeout → low context precision"},
        {"call_id": "call_a1b2c3", "time": "14:23", "query": "best python web framework 2026", "pass": "✅", "score": 0.82, "note": "Good coverage, some low-ranks irrelevant"},
        {"call_id": "call_d4e5f6", "time": "14:25", "query": "FastAPI middleware TypeError...", "pass": "✅", "score": 0.91, "note": "Excellent results, top answer is exact fix"},
    ]
    st.dataframe(
        pd.DataFrame(overview_data),
        column_config={
            "call_id": "Call ID",
            "time": "Time",
            "query": "Query",
            "pass": "Pass",
            "score": st.column_config.NumberColumn("Score", format="%.2f"),
            "note": "Eval Note",
        },
        use_container_width=True,
        hide_index=True,
    )


# ─── PAGE 2: CALL QUALITY STORY ──────────────────────────────────────────────

def render_call_quality(call):
    details = FINAL_RESULTS.get(call["call_id"], [])
    providers = PROVIDER_RESULTS.get(call["call_id"], [])
    eval_data = EVAL_RESULTS.get(call["call_id"])
    rewrites = REWRITE_VARIANTS.get(call["call_id"], [])

    verdict = "✅ PASS" if eval_data and eval_data["passed"] else "❌ FAIL"
    verdict_color = "#1b5e20" if eval_data and eval_data["passed"] else "#b71c1c"
    score = eval_data["pass_rate_score"] if eval_data else 0

    st.markdown(f"<h1 style='color:{verdict_color}; font-size:3em'>{verdict}</h1>", unsafe_allow_html=True)
    st.markdown(f"**Quality Score: {score:.2f}** — {eval_data['human_readable'] if eval_data else 'Not evaluated'}")

    with st.container(border=1):
        st.markdown("#### What was asked")
        st.markdown(f"> **{call['query']}**")
        st.caption(f"Goal: {call['research_goal']}")
        col1, col2, col3 = st.columns(3)
        col1.caption(f"Tool profile: `{call['tool_profile']}`")
        col2.caption(f"Providers: `{', '.join(call['providers_requested'])}`")
        col3.caption(f"Results requested: {call['num_results']}")

    if eval_data and not eval_data["passed"]:
        with st.container(border=1):
            st.markdown("#### Why it failed")
            for cat in eval_data["failure_categories"]:
                if cat == "low_context_precision":
                    st.markdown("- **Context Precision** — too many irrelevant results in top positions. Consider domain filters.")
                elif cat == "low_answer_relevance":
                    st.markdown("- **Answer Relevance** — results don't match the question's intent. Consider query rewrite.")
                elif cat == "partial_coverage":
                    st.markdown("- **Partial Coverage** — missing results from providers that timed out. Consider fallback providers.")
                elif cat == "low_faithfulness":
                    st.markdown("- **Faithfulness** — answers are making claims not supported by sources.")
                else:
                    st.markdown(f"- **{cat}**")

    with st.container(border=1):
        st.markdown("#### What came back")
        df = pd.DataFrame(details)
        st.dataframe(
            df,
            column_config={
                "rank": st.column_config.NumberColumn("#", width="small"),
                "url": st.column_config.LinkColumn("URL", width="medium"),
                "title": st.column_config.TextColumn("Title", width="medium"),
                "snippet": st.column_config.TextColumn("Snippet", width="large"),
                "score": st.column_config.NumberColumn("Score", format="%.2f", width="small"),
                "rerank_method": st.column_config.TextColumn("Rerank", width="small"),
                "origin_provider": st.column_config.TextColumn("Source", width="small"),
            },
            use_container_width=True,
            hide_index=True,
        )

    with st.container(border=1):
        st.markdown("#### Providers involved")
        pdf = pd.DataFrame(providers)
        if not pdf.empty:
            fig = px.bar(pdf, x="duration_ms", y="provider", orientation="h",
                         text="duration_ms", color="results_returned",
                         color_continuous_scale="blues",
                         labels={"duration_ms": "Latency (ms)", "provider": "Provider"})
            fig.update_traces(texttemplate="%{text}ms", textposition="outside")
            fig.update_layout(height=150, margin=dict(l=0, r=0, t=0, b=0))
            st.plotly_chart(fig, use_container_width=True)
        st.dataframe(pdf, column_config={
            "provider": "Provider",
            "duration_ms": st.column_config.NumberColumn("Latency (ms)", format="%.0f"),
            "results_returned": "Results",
            "error_message": st.column_config.TextColumn("Error", width="large"),
        }, use_container_width=True, hide_index=True)

    with st.expander("🔁 Pipeline internals (rerank, rewrite, funnel)"):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Survival funnel**")
            total = sum(p["results_returned"] for p in providers)
            stages = ["Provider", "Merged", "Reranked", "Final"]
            values = [total, max(total - 3, 4), max(total - 6, 3), len(details)]
            fig = go.Figure(go.Funnel(y=stages, x=values, textinfo="value+percent previous"))
            fig.update_layout(height=250, margin=dict(l=0, r=0, t=0, b=0))
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            if eval_data:
                st.markdown("**Eval breakdown**")
                metric_config = {
                    "Does the answer stick to sources? (faithfulness)": eval_data["faithfulness"],
                    "Are claims grounded in retrieved content? (groundedness)": eval_data["groundedness"],
                    "Are top results relevant? (context precision)": eval_data["context_precision"],
                    "Did we find the relevant content? (context recall)": eval_data["context_recall"],
                    "Does the answer address the question? (answer relevance)": eval_data["answer_relevance"],
                    "First relevant result position (MRR@10)": eval_data["mrr_at_10"],
                    "Overall ranking quality (nDCG@10)": eval_data["ndcg_at_10"],
                }
                for label, value in metric_config.items():
                    st.markdown(f"**{label}**: {value:.2f}")
                    st.progress(value)

        if rewrites:
            st.markdown("**Rewrite variants**")
            for rw in rewrites:
                tag = "✅ SELECTED" if rw["is_selected"] else ""
                st.markdown(f"- Variant {rw['variant_index']} {tag}: `{rw['variant_query']}` → score {rw['final_score']:.2f}")


# ─── APP ─────────────────────────────────────────────────────────────────────

PAGES = ["📊 Quality Overview", "🔍 Call Quality Story"]

with st.sidebar:
    st.title("Search Quality")
    page = st.radio("Page", PAGES, label_visibility="hidden")
    st.divider()

if page == "📊 Quality Overview":
    render_overview()
else:
    call_selector = {
        c["call_id"]: f"{c['query'][:60]}... | {c['duration_ms']}ms | {c['status']}"
        for c in SEARCH_CALLS
    }
    selected = st.sidebar.selectbox(
        "Select call", list(call_selector.keys()),
        format_func=lambda x: call_selector[x],
    )
    call = next(c for c in SEARCH_CALLS if c["call_id"] == selected)
    render_call_quality(call)
