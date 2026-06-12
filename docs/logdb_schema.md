# Process Logs DuckDB Schema

**Database:** `duckdb_data/logs/process_logs.duckdb`
**Table:** `process_logs`
**TTL:** 48 hours (auto-cleanup every ~5000 rows)

---

## Schema

```sql
CREATE TABLE process_logs (
    log_id        VARCHAR PRIMARY KEY,   -- UUID4 hex
    recorded_at   TIMESTAMP NOT NULL,    -- UTC, when the log was emitted
    logged_at     TIMESTAMP NOT NULL,    -- UTC, when the log was written to DB
    pid           INTEGER NOT NULL,      -- OS process ID
    logger_name   VARCHAR NOT NULL,      -- logger name (e.g. kindly_web_search_mcp_server.search.pipeline)
    level         VARCHAR NOT NULL,      -- DEBUG / INFO / WARNING / ERROR / CRITICAL
    message       VARCHAR NOT NULL,      -- formatted log message
    module        VARCHAR,               -- source module name
    func_name     VARCHAR,               -- source function name
    lineno        INTEGER,               -- source line number
    thread_name   VARCHAR,               -- thread name
    exception     VARCHAR,               -- full traceback text (if exc_info present)
    trace_id      VARCHAR,               -- OpenTelemetry trace_id (32-char hex)
    span_id       VARCHAR,               -- OpenTelemetry span_id (16-char hex)
    payload_json  VARCHAR                -- extra key-value pairs as JSON
);

CREATE INDEX idx_logs_time   ON process_logs (recorded_at);
CREATE INDEX idx_logs_level  ON process_logs (level);
CREATE INDEX idx_logs_logger ON process_logs (logger_name);
CREATE INDEX idx_logs_trace  ON process_logs (trace_id);
```

---

## Indexes

| Index | Column | Use |
|-------|--------|-----|
| `idx_logs_time` | `recorded_at` | Time-range queries (last hour, last 24h) |
| `idx_logs_level` | `level` | Filter by severity (ERROR+, WARNING+) |
| `idx_logs_logger` | `logger_name` | Filter by module/component |
| `idx_logs_trace` | `trace_id` | Correlate with OTEL traces |

---

## Typical Queries

Replace `$DB` with the absolute path:
```
/home/an/projects/web-search-mcp/duckdb_data/logs/process_logs.duckdb
```

### Error + Critical in the last hour

```sql
duckdb $DB -c "
  SELECT recorded_at, logger_name, message, exception
  FROM process_logs
  WHERE level IN ('ERROR', 'CRITICAL')
    AND recorded_at > now() - INTERVAL '1 hour'
  ORDER BY recorded_at DESC
  LIMIT 50;
"
```

### All logs from a specific module

```sql
duckdb $DB -c "
  SELECT recorded_at, level, message
  FROM process_logs
  WHERE logger_name LIKE '%query_execution%'
  ORDER BY recorded_at DESC
  LIMIT 100;
"
```

### Log volume by level (last 24h)

```sql
duckdb $DB -c "
  SELECT level, COUNT(*) AS cnt
  FROM process_logs
  WHERE recorded_at > now() - INTERVAL '24 hours'
  GROUP BY level
  ORDER BY cnt DESC;
"
```

### Logs correlated with a trace_id

```sql
duckdb $DB -c "
  SELECT recorded_at, level, logger_name, message
  FROM process_logs
  WHERE trace_id = 'YOUR_TRACE_ID_HERE'
  ORDER BY recorded_at;
"
```

### Full-text search across messages (substring)

```sql
duckdb $DB -c "
  SELECT recorded_at, level, message
  FROM process_logs
  WHERE message LIKE '%error%'
    AND recorded_at > now() - INTERVAL '1 hour'
  ORDER BY recorded_at DESC
  LIMIT 50;
"
```

### Logs around a specific time window

```sql
duckdb $DB -c "
  SELECT recorded_at, level, logger_name, message
  FROM process_logs
  WHERE recorded_at BETWEEN '2026-06-12T18:00:00' AND '2026-06-12T19:00:00'
  ORDER BY recorded_at;
"
```

### Exception traces only

```sql
duckdb $DB -c "
  SELECT recorded_at, logger_name, message, exception
  FROM process_logs
  WHERE exception IS NOT NULL
  ORDER BY recorded_at DESC
  LIMIT 20;
"
```

### Recent activity summary per logger

```sql
duckdb $DB -c "
  SELECT
    logger_name,
    level,
    COUNT(*) AS cnt,
    MIN(recorded_at) AS first_seen,
    MAX(recorded_at) AS last_seen
  FROM process_logs
  WHERE recorded_at > now() - INTERVAL '1 hour'
  GROUP BY logger_name, level
  ORDER BY last_seen DESC;
"
```

### Total row count and DB size

```sql
duckdb $DB -c "
  SELECT COUNT(*) AS total_rows FROM process_logs;
"
ls -lh $DB
```

---

## TTL Cleanup

Rows older than `process_logs_ttl_hours` (default: 48) are automatically
deleted every 50 batch flushes (~5000 rows), followed by `CHECKPOINT` to
reclaim disk space.

To manually trigger cleanup:

```sql
duckdb $DB -c "
  DELETE FROM process_logs WHERE recorded_at < now() - INTERVAL '48 hours';
  CHECKPOINT;
"
```

---

## Configuration

Env vars (optional, defaults shown):

```
PROCESS_LOGS_ENABLED=true          # set to 'false' to disable
PROCESS_LOGS_DUCKDB_PATH=...       # override DB path
PROCESS_LOGS_TTL_HOURS=48          # override TTL
```

Or in `settings.py`:

```python
process_logs_enabled: bool = True
process_logs_duckdb_path: str = DEFAULT_PROCESS_LOGS_DB   # duckdb_data/logs/process_logs.duckdb
process_logs_ttl_hours: int = 48
```
