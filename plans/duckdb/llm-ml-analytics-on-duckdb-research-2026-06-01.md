# LLM & ML Techniques for Analytics on DuckDB Data — Research Report

**Date:** 2026-06-01
**Context:** In-depth investigation into using LLMs and ML techniques for analytics on DuckDB data, specifically for the web-search-mcp observability pipeline. Covers DuckDB ML capabilities, Flock extension, NL2SQL patterns, prompt engineering, frameworks, and production observability analytics.

---

## 1. DuckDB Built-in ML/AI Capabilities & Extensions

### 1.1 Core DuckDB ML Foundation

DuckDB is not a native ML platform, but provides a high-performance data processing foundation with extensible architecture for ML integration:

| Capability | Description | Use for MCP Analytics |
|-----------|-------------|----------------------|
| **Feature Engineering** | High-performance SQL-based aggregation, joining, filtering, missing value imputation, categorical encoding, feature scaling | Prepare analytics features from `search_events` |
| **Statistics Extension** | Basic statistical functions, regressions, correlations, descriptive statistics in SQL | Provider latency statistics, error rate correlations |
| **Window Functions** | `ROWS BETWEEN N PRECEDING`, `RANGE INTERVAL`, cumulative sums, moving averages | Time-series trending, rolling baselines |
| **Zero-Copy Arrow/Polars** | Direct conversion to Polars/PyArrow DataFrames | Feed ML models without serialization overhead |

### 1.2 ML Extensions

| Extension | Status | Capabilities | Relevance |
|-----------|--------|-------------|-----------|
| **mlpack** | Experimental (community) | AdaBoost, random forests, regularized linear/logistic regression — fit and predict in SQL | Classification of error types, provider health prediction |
| **quackML** | Community | Linear/logistic regression, XGBoost, LightGBM, Hugging Face integration for text tasks and embeddings | Full ML pipeline within DuckDB |
| **statistics** | Official | Regressions, correlations, descriptive stats | Basic statistical analytics |
| **Anofox Forecast** | New (Nov 2025) | 31 time-series forecasting models, data preparation, EDA, evaluation metrics — pure SQL, no Python | Cache hit rate forecasting, provider latency prediction |
| **Flock/FlockMTL** | Active (DAIS Lab, Polytechnique Montréal) | LLM-in-SQL: `llm_complete`, `llm_filter`, `llm_embedding`, `llm_rerank`, `llm_reduce` | Semantic analysis of search results, content quality scoring |
| **Lance** | Community | Read/write Lance tables, vector search, full-text search, hybrid search from SQL | Bridge between LanceDB caches and DuckDB analytics |
| **VSS** | Official | HNSW indexing for vector similarity search | Embedding-based analytics on cached content |

### 1.3 DuckDB as ML Pipeline Component

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│ search_events│────▶│ Feature      │────▶│ ML Model    │────▶│ Insights     │
│ (DuckDB)     │     │ Engineering  │     │ (sklearn/   │     │ (DuckDB      │
│              │     │ (DuckDB SQL) │     │  PyTorch)   │     │  tables)     │
└─────────────┘     └──────────────┘     └─────────────┘     └──────────────┘
       │                    │                    │                    │
       ▼                    ▼                    ▼                    ▼
  Raw events         SQL features         Predictions          Stored results
  (18 columns +      (window stats,       (clusters,           (queryable via
   payload_json)      aggregates)          classifications)     SQL)
```

**Key insight:** DuckDB excels as the **preprocessing and feature engineering layer** (SQL-native), with ML computation delegated to Python libraries via zero-copy Arrow interchange. The `mlpack` and `quackML` extensions bring some models in-database, but are experimental.

---

## 2. Flock Extension: LLM-in-SQL for Observability Analytics

### 2.1 FlockMTL Architecture

FlockMTL (DAIS Lab, Polytechnique Montréal, VLDB 2025) deeply integrates LLMs into DuckDB SQL:

| Feature | Description | MCP Analytics Application |
|---------|-------------|--------------------------|
| **MODEL objects** | `CREATE MODEL` as first-class SQL entities | Define analytics models (e.g., `content_quality_scorer`, `error_classifier`) |
| **PROMPT objects** | `CREATE PROMPT` as reusable SQL entities | Define analytics prompts (e.g., `classify_search_intent`, `score_content_quality`) |
| **`llm_complete`** | Per-row text generation | Generate summaries of search sessions, explain anomalies |
| **`llm_filter`** | Semantic boolean filtering in WHERE clauses | Filter events by semantic criteria (e.g., "Is this search result relevant?") |
| **`llm_embedding`** | Generate embeddings for text columns | Embed queries for semantic clustering, similarity analysis |
| **`llm_rerank`** | Aggregate ranking function | Rerank cached results by relevance to current context |
| **`llm_reduce`** | Aggregate summarization with GROUP BY | Summarize provider performance per day, aggregate error patterns |
| **`llm_complete_json`** | Structured JSON output | Extract structured analytics from unstructured `payload_json` |
| **Built-in observability** | Track token usage, latency, call counts per query | Monitor LLM analytics cost |

### 2.2 Performance Optimizations

- **Automatic batching:** Up to 48x speedup for embedding functions through automatic batching of LLM API requests
- **KV-cache-friendly meta-prompting:** Consistent prefix structure for KV-cache reuse across rows
- **Provider support:** OpenAI, Azure, Ollama (local), Anthropic/Claude
- **Caching:** Built-in result caching to avoid redundant LLM calls

### 2.3 Concrete MCP Analytics Use Cases with Flock

```sql
-- 1. Semantic content quality scoring
CREATE MODEL content_scorer (TYPE openai, MODEL_NAME 'gpt-4o-mini');
CREATE PROMPT score_quality (PROMPT 'Rate this web search result quality 1-5:
  Query: {query}
  Result title: {title}
  Snippet: {snippet}
  Return JSON: {"score": int, "reason": str}');

SELECT
    event_id, query,
    llm_complete(
        {'model_name': 'content_scorer'},
        {'prompt_name': 'score_quality',
         'context_columns': [
            {'data': query},
            {'data': json_extract_string(payload_json, '$.results[0].title')},
            {'data': json_extract_string(payload_json, '$.results[0].snippet')}
         ]}
    ) AS quality_assessment
FROM search_events
WHERE event_name = 'tool.web_search.response'
LIMIT 100;

-- 2. Semantic error classification
SELECT event_id,
    llm_filter(
        {'model_name': 'gpt-4o-mini'},
        {'prompt': 'Is this error caused by a rate limit or authentication issue?',
         'context_columns': [{'data': json_extract_string(payload_json, '$.error_message')}]}
    ) AS is_rate_limit_error
FROM search_events
WHERE event_name LIKE '%.error';

-- 3. Query intent clustering via embeddings
SELECT
    event_id, query,
    llm_embedding({'model_name': 'text-embedding-3-small'},
        {'context_columns': [{'data': query}]}) AS query_embedding
FROM search_events
WHERE event_name = 'tool.web_search.request'
  AND recorded_at >= CURRENT_DATE - INTERVAL '7 days';

-- 4. Daily provider performance summaries
SELECT
    date_trunc('day', recorded_at) AS day,
    provider,
    llm_reduce(
        {'model_name': 'gpt-4o-mini'},
        {'prompt': 'Summarize the performance of this search provider today',
         'context_columns': [{'data': STRING_AGG(
            json_extract_string(payload_json, '$.results[0].title'), ', '
         )}]}
    ) AS daily_summary
FROM search_events
WHERE event_name = 'provider.search.result'
GROUP BY day, provider;
```

### 2.4 Flock Installation & Compatibility Note

```sql
INSTALL flock FROM community;
LOAD flock;
```

**Compatibility concern:** The project pins DuckDB `<1.5.3` for MotherDuck compatibility. Flock is based on DuckDB v1.4.4, so it should be compatible. However, the `flock` community extension may require a newer DuckDB version. **Test before adopting.**

---

## 3. LLM-Driven SQL Analytics (NL2SQL / Text-to-SQL)

### 3.1 LangChain SQL Agent Architecture

The dominant production pattern for LLM-driven SQL analytics:

```
User Question (NL)
       │
       ▼
┌──────────────────────────────────────────────────┐
│  LangChain SQL Agent                              │
│  ┌─────────┐  ┌──────────┐  ┌────────────────┐  │
│  │ Schema  │──▶│ SQL      │──▶│ Query          │  │
│  │ Lookup  │  │ Generate │  │ Execute+Check  │  │
│  │ Tool    │  │ (LLM)    │  │ (self-correct) │  │
│  └─────────┘  └──────────┘  └────────────────┘  │
│       │              │              │             │
│       ▼              ▼              ▼             │
│  DuckDB Schema  DuckDB SQL    DuckDB Results     │
└──────────────────────────────────────────────────┘
       │
       ▼
  Natural Language Answer
```

### 3.2 Production Best Practices (2025-2026)

| Practice | Detail | MCP Application |
|----------|--------|-----------------|
| **Agentic loop** | LLM inspects schema → drafts SQL → executes → reads errors → self-corrects → presents answer | More reliable than one-shot SQL generation |
| **Dialect-specific prompts** | Explicitly instruct LLM to generate DuckDB SQL (not PostgreSQL/MySQL) | DuckDB has unique functions: `COLUMNS()`, `EXCLUDE`, `REPLACE`, `SUMMARIZE` |
| **Progressive schema disclosure** | Dynamically load only relevant tables/columns via tool calls | Prevents context overflow with 18-column `search_events` + large `payload_json` |
| **Read-only connections** | Enforce at DB connection level, not just prompt level | Prevents LLM from accidentally dropping analytics tables |
| **SQL validation** | Pre-execution query checker (LangChain `SQLChecker`) | Catches syntax errors before hitting DuckDB |
| **Error feedback loop** | Feed execution errors back to LLM for correction | LLM learns from DuckDB-specific error messages |
| **Schema embedding + RAG** | Embed table metadata in vector DB, retrieve relevant schema per query | For large schemas with many analytics views |

### 3.3 LangChain DuckDB Integration Code

```python
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_openai import ChatOpenAI
from langchain.agents import create_sql_agent, AgentExecutor

# Connect to analytics DuckDB (read-only)
db = SQLDatabase.from_uri(
    "duckdb:///./.kindly/analytics/search_events.duckdb",
    include_tables=["search_events"],
    sample_rows_in_table_info=3,
)

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
toolkit = SQLDatabaseToolkit(db=db, llm=llm)

# DuckDB-specific system prompt
DUCKDB_SYSTEM_PROMPT = """You are a DuckDB SQL expert analyzing web search MCP observability data.

IMPORTANT DuckDB-specific syntax:
- Use `json_extract_string(payload_json, '$.field')` for JSON fields
- Use `date_trunc('hour', recorded_at)` for time bucketing
- Use `PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY col)` for percentiles
- Use `COUNT(*) FILTER (WHERE condition)` for conditional counts
- Use `LIST()` for array aggregation
- String concatenation uses `||` not `CONCAT()`

The `search_events` table has these columns:
event_id, event_name, recorded_at, run_key, tool_name, phase,
query, normalized_query, research_goal, provider, model,
duration_ms, input_count, output_count, trace_id, span_id,
cache_hit, payload_json

Common event_name values:
- tool.web_search.request / response (cache_hit: exact/semantic/miss)
- tool.get_content.request / response
- provider.search.result / error
- search.orchestrator.plan / response
- search.rerank.stage / summary
- query.rewrite.completed / error

Answer questions about search performance, cache efficiency, provider health,
content quality, and pipeline bottlenecks."""

agent = create_sql_agent(
    llm=llm,
    toolkit=toolkit,
    prompt=DUCKDB_SYSTEM_PROMPT,
    verbose=True,
    handle_parsing_errors=True,
)

# Example queries
agent.invoke("What's the cache hit rate for the last 7 days by cache type?")
agent.invoke("Which provider has the highest p95 latency?")
agent.invoke("Show me the top 10 domains that fail content extraction")
```

### 3.4 Security Considerations

| Risk | Mitigation |
|------|-----------|
| Prompt injection → destructive SQL | Read-only DuckDB connection (`access_mode=read_only`) |
| Data exfiltration via LLM | Sandbox DuckDB in Docker, limit result row counts |
| API key exposure | Environment variables only, never in prompts |
| Unbounded queries | `LIMIT` enforcement, query timeout |

---

## 4. LLMs for Observability & Telemetry Analytics

### 4.1 Production Patterns (2025-2026)

LLMs are transforming observability from reactive monitoring to proactive intelligence:

| Pattern | Description | MCP Application |
|---------|-------------|-----------------|
| **Proactive anomaly detection** | LLMs analyze historical telemetry to predict issues before they occur | Predict provider degradation from error rate trends |
| **Natural language querying** | Engineers ask "Why is the checkout service slow?" in plain English | "Why are cache misses increasing?" → SQL query → insight |
| **Intelligent cost optimization** | AI identifies unused metrics, optimizes retention policies | Identify rarely-used analytics events for pruning |
| **Enhanced business observability** | Observability data analyzed by LLMs for strategic insights | "Which search queries produce the lowest quality results?" |
| **LLM-specific monitoring** | Track token usage, cost, latency, model info for LLM calls | Monitor Flock LLM analytics cost via `flock_get_metrics()` |

### 4.2 OpenTelemetry + LLM Analytics Stack

```
┌─────────────────────────────────────────────────────────────┐
│                    Data Collection Layer                      │
│  OpenTelemetry SDK → Traces + Metrics + Logs                │
│  OpenLLMetry → LLM-specific attributes (tokens, model, etc) │
│  GenAI Semantic Conventions → Standardized LLM telemetry      │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Storage Layer                              │
│  DuckDB (.duckdb) → Local analytics, SQL queries            │
│  MotherDuck → Cloud analytics, Grafana dashboards           │
│  Grafana Cloud → OTLP traces/metrics/logs                   │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Analysis Layer                             │
│  LangChain SQL Agent → NL2SQL on DuckDB                     │
│  Flock Extension → LLM-in-SQL semantic analysis             │
│  Pre-built queries → Automated analytics reports            │
│  Grafana dashboards → Visual monitoring                     │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 DuckDB as Observability Analytics Engine

DuckDB is emerging as a valuable tool specifically for observability analytics:

- **Embedded analytics for LLM agents:** DuckDB serves as memory layer and analytical engine within LLM agent applications
- **In-process data processing:** No external infrastructure needed, efficient on-disk and in-memory processing
- **Flock extension:** Integrates analytics with semantic analysis directly through declarative SQL, with built-in LLM observability features (token usage, latency, call counts tracking)
- **Efficient log/metric analysis:** Column-oriented storage and vectorized execution for analytical queries over structured logs and metrics

### 4.4 Observability Platforms Built on OpenTelemetry

| Platform | DuckDB Integration | LLM Analytics |
|----------|-------------------|---------------|
| **Grafana Cloud** | MotherDuck DuckDB datasource plugin | Alert rules, anomaly detection |
| **Langfuse** | OpenTelemetry-compatible | LLM-specific tracing, cost tracking |
| **Traceloop** | OpenLLMetry-based | LLM call tracing, quality evaluation |
| **LangWatch** | OpenTelemetry export | LLM output quality monitoring |
| **Dynatrace** | AI-powered observability | Proactive anomaly detection |

---

## 5. Prompt Engineering for DuckDB Data Analysis

### 5.1 Schema-Aware Prompts (Most Important)

```python
DUCKDB_ANALYTICS_SYSTEM_PROMPT = """You are an expert data analyst specializing in
web search MCP observability. You analyze the `search_events` DuckDB table.

## Schema
Table: search_events (18 columns + JSON payload)
- event_id: UUID primary key
- event_name: Event type (e.g., 'tool.web_search.response')
- recorded_at: TIMESTAMP with timezone
- run_key: Correlates events within a single search run
- tool_name: MCP tool name (web_search, get_content, etc.)
- phase: request or response
- query: Original user query
- normalized_query: Lowercased, trimmed query
- research_goal: User's stated research goal
- provider: Search provider name (searxng, tavily, brave, etc.)
- model: LLM model used (if applicable)
- duration_ms: Operation duration in milliseconds
- input_count: Input items count
- output_count: Output items count
- trace_id: OpenTelemetry trace ID
- span_id: OpenTelemetry span ID
- cache_hit: 'true' or 'false'
- payload_json: JSON string with event-specific data

## DuckDB SQL Dialect Rules
- JSON access: json_extract_string(payload_json, '$.field')
- Time bucketing: date_trunc('hour', recorded_at)
- Percentiles: PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY col)
- Conditional counts: COUNT(*) FILTER (WHERE condition)
- Array aggregation: LIST(col) or STRING_AGG(col, ',')
- String concat: col1 || col2 (not CONCAT())
- Interval: INTERVAL '7 days', INTERVAL '1 hour'
- Null-safe division: a / NULLIF(b, 0)

## Common Analytics Queries
- Cache hit rate: COUNT(*) FILTER (WHERE cache_hit='true') / COUNT(*)
- Provider p95: PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms)
- Error rate: COUNT(*) FILTER (WHERE event_name LIKE '%.error') / COUNT(*)
- Domain extraction: REGEXP_EXTRACT(url, 'https?://([^/]+)', 1)

## Output Rules
- Always generate valid DuckDB SQL
- Use NULLIF to prevent division by zero
- Include LIMIT for exploratory queries
- Use date_trunc for time-series grouping
- Prefer FILTER (WHERE) over CASE WHEN for conditional aggregation
"""
```

### 5.2 Iterative Refinement Pattern

```python
# Step 1: Generate SQL
sql = llm.generate(f"{system_prompt}\n\nUser question: {question}")

# Step 2: Execute and capture errors
try:
    result = duckdb.execute(sql).fetchdf()
except Exception as e:
    # Step 3: Feed error back to LLM
    corrected_sql = llm.generate(
        f"The previous query failed with error:\n{str(e)}\n\n"
        f"Original query:\n{sql}\n\n"
        f"Please fix the query."
    )
    result = duckdb.execute(corrected_sql).fetchdf()
```

### 5.3 Few-Shot Examples for MCP Analytics

```python
FEW_SHOT_EXAMPLES = """
## Example 1: Cache hit rate trend
Q: "What's the daily cache hit rate for the last 30 days?"
SQL:
SELECT
    date_trunc('day', recorded_at) AS day,
    COUNT(*) FILTER (WHERE cache_hit = 'true') AS hits,
    COUNT(*) FILTER (WHERE cache_hit = 'false') AS misses,
    ROUND(100.0 * hits / NULLIF(hits + misses, 0), 2) AS hit_rate_pct
FROM search_events
WHERE event_name LIKE 'tool.web_search.response'
  AND recorded_at >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY 1 ORDER BY 1

## Example 2: Provider latency comparison
Q: "Compare p50 and p95 latency across all providers this week"
SQL:
SELECT
    provider,
    COUNT(*) AS calls,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
    ROUND(p95_ms / NULLIF(p50_ms, 0), 2) AS tail_ratio
FROM search_events
WHERE event_name IN ('provider.search.result', 'provider.search.error')
  AND recorded_at >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY provider ORDER BY p95_ms

## Example 3: Content extraction quality
Q: "Which fetch backends have the best success rate?"
SQL:
SELECT
    json_extract_string(payload_json, '$.fetch_backend') AS backend,
    COUNT(*) AS fetches,
    COUNT(*) FILTER (WHERE json_extract_string(payload_json, '$.status') = 'success') AS success,
    ROUND(100.0 * success / NULLIF(COUNT(*), 0), 1) AS success_rate
FROM search_events
WHERE event_name = 'tool.get_content.response'
  AND recorded_at >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY 1 ORDER BY success_rate DESC
"""
```

### 5.4 Chain-of-Thought for Complex Analytics

```python
COMPLEX_ANALYTICS_COT_PROMPT = """For complex analytics questions, think step by step:

1. IDENTIFY the relevant event_name values and time range
2. DETERMINE which columns and JSON fields are needed
3. CHOOSE the right DuckDB aggregation functions
4. WRITE the SQL with proper NULL handling and time bucketing
5. VERIFY the query will return meaningful results

Example reasoning:
Q: "Are there any providers whose error rate spiked in the last 24 hours compared to the 7-day baseline?"

Step 1: Need 'provider.search.result' and 'provider.search.error' events
Step 2: Need provider, event_name, recorded_at columns
Step 3: Need hourly error rates, then compare recent vs baseline
Step 4: Use CTEs for hourly stats, window functions for rolling averages
Step 5: Flag hours where error_rate > 2x the 7-day baseline
"""
```

---

## 6. ML Techniques for DuckDB Analytics

### 6.1 Time Series Analysis

DuckDB's native SQL capabilities are strong for time series:

```sql
-- Moving average with window functions
SELECT
    date_trunc('hour', recorded_at) AS hour,
    COUNT(*) AS events,
    AVG(COUNT(*)) OVER (
        ORDER BY date_trunc('hour', recorded_at)
        ROWS BETWEEN 23 PRECEDING AND CURRENT ROW
    ) AS rolling_24h_avg
FROM search_events
GROUP BY 1 ORDER BY 1;

-- Seasonal decomposition (requires Anofox Forecast extension)
-- INSTALL anofox_forecast FROM community; LOAD anofox_forecast;
-- SELECT * FROM anofox_forecast(
--     (SELECT date_trunc('hour', recorded_at) AS ts, COUNT(*) AS value
--      FROM search_events GROUP BY 1),
--     model := 'auto'
-- );
```

### 6.2 Anomaly Detection

Multiple approaches, from simple SQL to ML-based:

**Approach 1: Statistical (SQL-native)**
```sql
-- Z-score anomaly detection on hourly error rates
WITH hourly AS (
    SELECT date_trunc('hour', recorded_at) AS hour,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE event_name LIKE '%.error') AS errors,
        ROUND(100.0 * errors / NULLIF(total, 0), 2) AS error_rate
    FROM search_events GROUP BY 1
),
stats AS (
    SELECT AVG(error_rate) AS mean, STDDEV(error_rate) AS stddev FROM hourly
)
SELECT h.hour, h.error_rate,
    ROUND((h.error_rate - s.mean) / NULLIF(s.stddev, 0), 2) AS z_score,
    (ABS(h.error_rate - s.mean) > 3 * s.stddev) AS is_anomaly
FROM hourly h CROSS JOIN stats s
WHERE h.hour >= CURRENT_DATE - INTERVAL '24 hours'
ORDER BY h.hour DESC;
```

**Approach 2: ML-based (Python + DuckDB)**
```python
import duckdb
import numpy as np
from sklearn.ensemble import IsolationForest

con = duckdb.connect(".kindly/analytics/search_events.duckdb")

# Extract features via DuckDB SQL
features_df = con.sql("""
    SELECT
        date_trunc('hour', recorded_at) AS hour,
        COUNT(*) AS total_events,
        COUNT(*) FILTER (WHERE event_name LIKE '%.error') AS error_count,
        AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL) AS avg_duration,
        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_duration,
        COUNT(DISTINCT provider) AS active_providers,
        COUNT(*) FILTER (WHERE cache_hit = 'true') AS cache_hits
    FROM search_events
    WHERE recorded_at >= CURRENT_DATE - INTERVAL '30 days'
    GROUP BY 1
""").pl()

# Train Isolation Forest for anomaly detection
model = IsolationForest(contamination=0.05, random_state=42)
feature_cols = ['total_events', 'error_count', 'avg_duration', 'p95_duration',
                'active_providers', 'cache_hits']
features_df = features_df.with_columns(
    pl.Series('anomaly_score', model.fit_predict(features_df.select(feature_cols).to_numpy()))
)

# Store results back in DuckDB
con.sql("CREATE TABLE IF NOT EXISTS hourly_anomaly_scores AS SELECT * FROM features_df")
```

### 6.3 Clustering (Query Intent / Session Patterns)

```python
import duckdb
from sklearn.cluster import KMeans
from sentence_transformers import SentenceTransformer

con = duckdb.connect(".kindly/analytics/search_events.duckdb")

# Extract queries
queries = con.sql("""
    SELECT DISTINCT query FROM search_events
    WHERE event_name = 'tool.web_search.request'
      AND recorded_at >= CURRENT_DATE - INTERVAL '30 days'
      AND query IS NOT NULL AND query != ''
""").pl()['query'].to_list()

# Generate embeddings
model = SentenceTransformer('all-MiniLM-L6-v2')
embeddings = model.encode(queries)

# Cluster
kmeans = KMeans(n_clusters=10, random_state=42)
labels = kmeans.fit_predict(embeddings)

# Store cluster assignments
import polars as pl
cluster_df = pl.DataFrame({'query': queries, 'cluster': labels})
con.sql("CREATE TABLE IF NOT EXISTS query_clusters AS SELECT * FROM cluster_df")

# Analyze clusters
con.sql("""
    SELECT qc.cluster,
        COUNT(*) AS query_count,
        LIST(qc.query ORDER BY RANDOM() LIMIT 5) AS sample_queries
    FROM query_clusters qc
    GROUP BY qc.cluster ORDER BY query_count DESC
""").show()
```

### 6.4 Classification (Error Type / Content Quality)

```python
import duckdb
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split

con = duckdb.connect(".kindly/analytics/search_events.duckdb")

# Extract features for error classification
errors_df = con.sql("""
    SELECT
        event_name,
        provider,
        duration_ms,
        json_extract_string(payload_json, '$.error_type') AS error_type,
        LENGTH(json_extract_string(payload_json, '$.error_message')) AS error_msg_length,
        CASE WHEN json_extract_string(payload_json, '$.error_message') LIKE '%rate%' THEN 1 ELSE 0 END AS has_rate_keyword,
        CASE WHEN json_extract_string(payload_json, '$.error_message') LIKE '%auth%' THEN 1 ELSE 0 END AS has_auth_keyword,
        CASE WHEN json_extract_string(payload_json, '$.error_message') LIKE '%timeout%' THEN 1 ELSE 0 END AS has_timeout_keyword
    FROM search_events
    WHERE event_name LIKE '%.error'
      AND recorded_at >= CURRENT_DATE - INTERVAL '90 days'
""").pl()

# Train classifier to predict error category
feature_cols = ['duration_ms', 'error_msg_length', 'has_rate_keyword',
                'has_auth_keyword', 'has_timeout_keyword']
X = errors_df.select(feature_cols).to_numpy()
y = errors_df['error_type'].to_list()

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)
clf = GradientBoostingClassifier(n_estimators=100, random_state=42)
clf.fit(X_train, y_train)

# Use DuckDB UDF for batch classification
con.create_function('classify_error', clf.predict, return_type='VARCHAR')
```

### 6.5 Provider Health Scoring (Composite ML Model)

```python
import duckdb
import polars as pl
from sklearn.preprocessing import StandardScaler

con = duckdb.connect(".kindly/analytics/search_events.duckdb")

# Build provider health features
health_df = con.sql("""
    SELECT
        provider,
        date_trunc('day', recorded_at) AS day,
        COUNT(*) AS total_calls,
        COUNT(*) FILTER (WHERE event_name = 'provider.search.error') AS error_count,
        ROUND(100.0 * error_count / NULLIF(total_calls, 0), 2) AS error_rate,
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
        PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY duration_ms) AS p99_ms,
        AVG(output_count) FILTER (WHERE event_name = 'provider.search.result') AS avg_results,
        PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY duration_ms)
            / NULLIF(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_ms), 0) AS tail_ratio
    FROM search_events
    WHERE event_name IN ('provider.search.result', 'provider.search.error')
      AND recorded_at >= CURRENT_DATE - INTERVAL '30 days'
    GROUP BY 1, 2
""").pl()

# Composite health score (weighted formula)
health_df = health_df.with_columns(
    ((1 - pl.col('error_rate') / 100) * 0.3 +
     (1 - pl.col('p95_ms').clip(0, 10000) / 10000) * 0.25 +
     (pl.col('avg_results').clip(0, 20) / 20) * 0.25 +
     (1 - pl.col('tail_ratio').clip(1, 10) / 10) * 0.2
    ).alias('health_score')
)

con.sql("CREATE OR REPLACE TABLE provider_health_scores AS SELECT * FROM health_df")
```

---

## 7. Framework Integration Patterns

### 7.1 LangChain + DuckDB Analytics Agent

```python
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_openai import ChatOpenAI
from langchain.agents import create_sql_agent

db = SQLDatabase.from_uri(
    "duckdb:///./.kindly/analytics/search_events.duckdb?access_mode=read_only"
)
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
toolkit = SQLDatabaseToolkit(db=db, llm=llm)
agent = create_sql_agent(llm=llm, toolkit=toolkit, verbose=True)
```

### 7.2 LlamaIndex + DuckDB (via SQL Database)

```python
from llama_index.core import SQLDatabase
from llama_index.core.query_engine import NLSQLTableQueryEngine
from llama_index.llms.openai import OpenAI
import duckdb

# Connect to DuckDB
conn = duckdb.connect(".kindly/analytics/search_events.duckdb")
sql_database = SQLDatabase(conn, include_tables=["search_events"])

llm = OpenAI(model="gpt-4o-mini", temperature=0)
query_engine = NLSQLTableQueryEngine(
    sql_database=sql_database,
    tables=["search_events"],
    llm=llm,
)

response = query_engine.query(
    "What are the top 5 domains by content extraction success rate?"
)
```

### 7.3 Custom Analytics Pipeline (DuckDB + Python ML)

```python
import duckdb
import polars as pl
from sklearn.ensemble import IsolationForest
from sentence_transformers import SentenceTransformer

class MCPAnalyticsPipeline:
    """End-to-end analytics pipeline: DuckDB SQL → Python ML → DuckDB results."""

    def __init__(self, duckdb_path: str):
        self.con = duckdb.connect(duckdb_path)

    def extract_features(self, days: int = 30) -> pl.DataFrame:
        """Extract time-series features via DuckDB SQL."""
        return self.con.sql(f"""
            SELECT
                date_trunc('hour', recorded_at) AS hour,
                COUNT(*) AS total_events,
                COUNT(*) FILTER (WHERE event_name LIKE '%.error') AS errors,
                AVG(duration_ms) AS avg_latency,
                COUNT(DISTINCT provider) AS providers_active,
                COUNT(*) FILTER (WHERE cache_hit = 'true') AS cache_hits
            FROM search_events
            WHERE recorded_at >= CURRENT_DATE - INTERVAL '{days} days'
            GROUP BY 1 ORDER BY 1
        """).pl()

    def detect_anomalies(self, features: pl.DataFrame) -> pl.DataFrame:
        """Run Isolation Forest anomaly detection."""
        model = IsolationForest(contamination=0.05, random_state=42)
        cols = ['total_events', 'errors', 'avg_latency', 'providers_active', 'cache_hits']
        scores = model.fit_predict(features.select(cols).to_numpy())
        return features.with_columns(pl.Series('is_anomaly', scores == -1))

    def cluster_queries(self, days: int = 30) -> pl.DataFrame:
        """Cluster search queries by semantic similarity."""
        queries = self.con.sql(f"""
            SELECT DISTINCT query FROM search_events
            WHERE event_name = 'tool.web_search.request'
              AND recorded_at >= CURRENT_DATE - INTERVAL '{days} days'
        """).pl()['query'].to_list()

        model = SentenceTransformer('all-MiniLM-L6-v2')
        embeddings = model.encode(queries)

        from sklearn.cluster import KMeans
        kmeans = KMeans(n_clusters=10, random_state=42)
        labels = kmeans.fit_predict(embeddings)

        return pl.DataFrame({'query': queries, 'cluster': labels})

    def store_results(self, table_name: str, df: pl.DataFrame) -> None:
        """Store ML results back in DuckDB."""
        self.con.sql(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM df")

    def run_full_pipeline(self) -> dict:
        """Run all analytics steps and return summary."""
        features = self.extract_features()
        anomalies = self.detect_anomalies(features)
        self.store_results('hourly_anomaly_scores', anomalies)

        clusters = self.cluster_queries()
        self.store_results('query_clusters', clusters)

        return {
            'anomalies_detected': anomalies.filter(pl.col('is_anomaly')).shape[0],
            'query_clusters': clusters['cluster'].n_unique(),
            'total_hours_analyzed': features.shape[0],
        }
```

---

## 8. Recommended Implementation for Web-Search MCP

### 8.1 Priority 1: Pre-Built SQL Analytics Module (Low Effort, High Value)

Create `analytics/queries.py` with parameterized DuckDB SQL queries (as detailed in the data audit report). This requires **no LLM or ML** — just SQL.

### 8.2 Priority 2: Flock Extension for Semantic Analytics (Medium Effort, High Value)

Install Flock and create MODEL/PROMPT objects for:
- Content quality scoring (`llm_complete` with structured JSON output)
- Error classification (`llm_filter` for semantic error categorization)
- Query intent embedding (`llm_embedding` for clustering input)

**Risk:** DuckDB version compatibility with MotherDuck pin (`<1.5.3`).

### 8.3 Priority 3: LangChain SQL Agent for Ad-Hoc Analytics (Medium Effort, Medium Value)

Build a LangChain SQL agent that can answer natural language questions about MCP performance by querying the local DuckDB analytics file. Expose as an MCP tool or CLI subcommand.

### 8.4 Priority 4: ML Pipeline for Automated Insights (High Effort, High Value)

Build the `MCPAnalyticsPipeline` class that:
1. Extracts features from `search_events` via DuckDB SQL
2. Runs anomaly detection (Isolation Forest) on hourly metrics
3. Clusters queries by semantic similarity
4. Scores provider health with composite metrics
5. Stores all results back in DuckDB for querying

### 8.5 Priority 5: NL2SQL MCP Tool (Medium Effort, Medium Value)

Expose analytics as an MCP tool:
```python
@mcp.tool(name="analytics_query")
def analytics_query(question: str) -> dict:
    """Ask questions about MCP performance in natural language."""
    # LangChain SQL agent queries DuckDB and returns results
```

---

## 9. Key Takeaways

1. **DuckDB is the right engine for analytics** — its SQL capabilities (window functions, percentiles, FILTER, JSON extraction) are sufficient for most observability analytics without ML
2. **Flock extension is the most promising LLM integration** — it enables semantic analysis directly in SQL with automatic batching and caching
3. **LangChain SQL agents are production-proven** for NL2SQL, but require careful DuckDB dialect prompting and read-only connections
4. **ML techniques (clustering, anomaly detection, classification) work best as Python post-processing** on DuckDB-extracted features, with results stored back in DuckDB
5. **The biggest gap is not ML — it's missing data** (cache events, session events, error classification) as identified in the data audit report
6. **Start with SQL analytics before adding LLM/ML** — the pre-built query module provides immediate value with zero external dependencies

---

## 10. Sources & References

- **DuckDB ML capabilities:** Gemini Search (grounded), duckdb.org documentation
- **Flock/FlockMTL:** GitHub `dais-polymtl/flock`, VLDB 2025 paper, duckdb.org community extensions
- **LangChain SQL Agent:** Gemini Search (grounded), langchain.com documentation, GitHub examples
- **LLM Observability:** Gemini Search (grounded), OpenTelemetry GenAI semantic conventions, OpenLLMetry
- **Prompt Engineering:** Gemini Search (grounded), IBM Research, Google Research, Snowflake best practices
- **ML Techniques:** Gemini Search (grounded), scikit-learn documentation, Anofox Forecast extension
- **GitHub Code:** `chatbi/chatbi` (ChatBI with DuckDB + LLM), `langchain-ai/deepagents` (text-to-sql skills), `zhangzihaoDT/SQL-Agent-with-DeepSeek`
