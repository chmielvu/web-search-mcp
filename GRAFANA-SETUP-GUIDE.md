# Grafana Cloud Observability Setup for web-search-mcp

Complete guide to set up Grafana Cloud monitoring - **NO Docker required**.

---

## Overview

Three approaches, from simplest to production:

| Approach | Signals | Collector | Best For |
|----------|---------|-----------|----------|
| **1. opentelemetry-instrument** | Traces + Metrics + Logs | None (SDK direct) | Development, testing |
| **2. OTLP SDK + PrometheusMetricReader** | Traces + Logs + Metrics (scraped) | Alloy | Production |
| **3. Full Alloy Collector** | All + enrichment + routing | Alloy | Enterprise |

---

## Approach 1: opentelemetry-instrument (QUICK START)

The fastest way - auto-instruments everything, sends directly to Grafana Cloud.

### Step 1: Install packages

```powershell
pip install opentelemetry-distro opentelemetry-exporter-otlp
opentelemetry-bootstrap -a install
```

### Step 2: Get Grafana Cloud credentials

Go to Grafana Cloud Portal → **My Account** → **OpenTelemetry** section:

- OTLP endpoint URL
- Instance ID  
- API token

### Step 3: Create Base64 auth header

```powershell
# PowerShell - create Base64 auth
$credentials = "<instance-id>:<api-token>"
$base64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($credentials))
Write-Output "Authorization=Basic $base64"
```

### Step 4: Run with auto-instrumentation

```powershell
$env:OTEL_SERVICE_NAME="web-search-mcp"
$env:OTEL_EXPORTER_OTLP_ENDPOINT="https://otlp-gateway-prod-us-east-0.grafana.net/otlp"
$env:OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <YOUR_BASE64>"
$env:OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf"

opentelemetry-instrument python -m kindly_web_search_mcp_server.server
```

Or in a single command:

```powershell
opentelemetry-instrument `
  --service-name web-search-mcp `
  --exporter-otlp-endpoint https://otlp-gateway-prod-us-east-0.grafana.net/otlp `
  --exporter-otlp-headers "Authorization=Basic <YOUR_BASE64>" `
  --exporter-otlp-protocol http/protobuf `
  python -m kindly_web_search_mcp_server.server
```

### Step 5: Generate traffic

Make some search requests. Data appears in **Application Observability** within 1 minute.

---

## Approach 2: Prometheus + OTLP (RECOMMENDED FOR PRODUCTION)

Use Alloy to scrape Prometheus metrics + OTLP for traces/logs. Better reliability and enrichment.

### Step 1: Install packages

```powershell
pip install opentelemetry-distro opentelemetry-exporter-otlp opentelemetry-exporter-prometheus
opentelemetry-bootstrap -a install
```

### Step 2: Install Grafana Alloy on Windows

**Option A: WinGet (recommended)**

```powershell
winget install GrafanaLabs.Alloy
```

**Option B: Manual download**

1. Download from https://github.com/grafana/alloy/releases
2. Get `alloy-installer-windows-amd64.exe`
3. Run as Administrator: `.\alloy-installer-windows-amd64.exe`

Alloy installs to `%PROGRAMFILES%\GrafanaLabs\Alloy`

### Step 3: Create Alloy config

Create `%PROGRAMFILES%\GrafanaLabs\Alloy\config.alloy`:

```alloy
// =============================================================================
// GRAFANA ALLOY CONFIG FOR web-search-mcp
// =============================================================================

// Scrape Prometheus metrics from web-search-mcp
prometheus.scrape "web_search_mcp" {
  targets = [{
    "__address__" = "localhost:9090",
    "job" = "web-search-mcp",
    "service" = "web-search-mcp",
  }]
  forward_to = [prometheus.remote_write.grafana_cloud.receiver]
  scrape_interval = "15s"
}

// Forward metrics to Grafana Cloud Prometheus
prometheus.remote_write "grafana_cloud" {
  endpoint {
    url = "https://<YOUR-PROM-URL>/api/prom/push"
    
    basic_auth {
      username = "<INSTANCE-ID>"
      password = "<API-TOKEN>"
    }
    
    // Retry on failures
    queue_config {
      max_samples_per_send = 1000
      max_retries = 10
    }
  }
}

// Receive OTLP traces/logs from web-search-mcp
otelcol.receiver.otlp "default" {
  http {
    endpoint = "0.0.0.0:4318"
  }
  grpc {
    endpoint = "0.0.0.0:4317"
  }

  output {
    traces = [otelcol.exporter.otlp.grafana_cloud_traces.input]
    logs = [otelcol.exporter.otlp.grafana_cloud_logs.input]
    metrics = [otelcol.processor.prometheus.web_search_mcp.input]  // Also convert OTLP metrics to Prometheus
  }
}

// Process OTLP metrics into Prometheus format (reduces duplication)
otelcol.processor.prometheus "web_search_mcp" {
  forward_to = [prometheus.remote_write.grafana_cloud.receiver]
}

// Export traces to Grafana Cloud Tempo
otelcol.exporter.otlp "grafana_cloud_traces" {
  client {
    endpoint = "https://otlp-gateway-prod-us-east-0.grafana.net/otlp"
    
    headers = {
      "Authorization" = "Basic <YOUR-BASE64-OTLP-TOKEN>",
    }
  }
}

// Export logs to Grafana Cloud Loki
otelcol.exporter.otlp "grafana_cloud_logs" {
  client {
    endpoint = "https://otlp-gateway-prod-us-east-0.grafana.net/otlp"
    
    headers = {
      "Authorization" = "Basic <YOUR-BASE64-OTLP-TOKEN>",
    }
  }
}
```

### Step 4: Get credentials from Grafana Cloud

1. **Prometheus metrics**: Grafana Cloud Portal → My Account → Prometheus section
   - Remote write URL
   - Instance ID
   - API token

2. **OTLP traces/logs**: Grafana Cloud Portal → My Account → OpenTelemetry section
   - OTLP endpoint
   - Generate token → Base64 encode

### Step 5: Start Alloy

```powershell
# Start Alloy service (installed as Windows service)
net start alloy

# Or run manually
alloy run "C:\Program Files\GrafanaLabs\Alloy\config.alloy"
```

### Step 6: Configure web-search-mcp

Add to `.env`:

```powershell
# OTLP endpoint (local Alloy collector)
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf

# Prometheus metrics port (for Alloy scraping)
KINDLY_PROMETHEUS_PORT=9090

# Service metadata
OTEL_SERVICE_NAME=web-search-mcp
```

### Step 7: Run with instrumentation

```powershell
opentelemetry-instrument python -m kindly_web_search_mcp_server.server
```

---

## How They Work Together

```
┌─────────────────────────────────────────────────────────────┐
│                   web-search-mcp                             │
│                                                             │
│   opentelemetry-instrument                                  │
│       ├── Auto-instrumented HTTP handlers                   │
│       ├── Custom spans for search operations                │
│       ├── PrometheusMetricReader (:9090/metrics)            │
│       └── OTLP exporter → localhost:4318                    │
│                                                             │
└──────────────────────┬──────────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          │                         │
          │ :9090/metrics           │ :4318 OTLP
          │ (Prometheus)            │ (Traces + Logs)
          ▼                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Grafana Alloy                              │
│                                                             │
│   prometheus.scrape ──► prometheus.remote_write             │
│                          (to Grafana Cloud Mimir)           │
│                                                             │
│   otelcol.receiver ──► otelcol.exporter.otlp                │
│                          (to Grafana Cloud Tempo/Loki)      │
│                                                             │
│   Benefits:                                                 │
│   - Retry on network failures                               │
│   - Enrich with k8s/host metadata                           │
│   - Buffer during network outages                           │
│   - Sample/redact sensitive data                            │
│                                                             │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                     Grafana Cloud                             │
│                                                             │
│   Tempo (traces)    Mimir (metrics)    Loki (logs)          │
│                                                             │
│   Application Observability dashboard:                       │
│   - Correlate trace → metrics → logs                        │
│   - Service map                                             │
│   - Latency, error rate, throughput                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## What You'll See in Grafana

### Application Observability (automatic)

Navigate to **Application Observability**:

- Service: `web-search-mcp`
- Operations: `web_search`, `provider.{name}`, `rrf_merge`
- Latency P50/P95/P99
- Error rate
- Throughput (requests/second)

### Traces (Tempo)

Each search shows:
- **web_search** span with query attribute
- **provider.searxng** span with duration, result_count
- **provider.gemini** span (success/error)
- **rrf_merge** span with merge duration

### Metrics (Mimir/Prometheus)

```promql
# Request rate
rate(web_search_requests_total[5m])

# Provider latency P95
histogram_quantile(0.95,
  sum(rate(web_search_provider_duration_seconds_bucket[5m])) by (le, provider)
)

# Provider success rate
sum(rate(web_search_provider_calls_total{status="success"}[5m]))
/ sum(rate(web_search_provider_calls_total[5m]))

# Results per provider
avg(web_search_provider_results) by (provider)
```

### Logs (Loki)

Structured logs with trace correlation:
```logfmt
timestamp=... level=INFO service=web-search-mcp trace_id=abc123 span_id=def456
event=provider_call provider=searxng duration_ms=826 result_count=10
```

---

## Correlation Example

1. See latency spike in Application Observability
2. Click trace → see `provider.gemini` took 2 seconds
3. Check metrics → `web_search_provider_calls_total{provider="gemini",status="error"}` spiked
4. View logs → "HTTP 500 Internal Server Error" from Gemini API
5. Root cause: Google API instability (not our code)

---

## Troubleshooting

### No traces appearing

1. Check Alloy is running: `net start alloy`
2. Verify endpoint: `http://localhost:4318/v1/traces` should return 405 (method not allowed, but endpoint exists)
3. Check Base64 auth header format: `Basic <base64(instance:token)>`

### No metrics appearing

1. Check port 9090 is listening: `curl http://localhost:9090/metrics`
2. Verify Alloy config targets match port
3. Check Grafana Cloud Prometheus credentials

### High memory usage

OTel SDK batches data. Adjust with:

```powershell
$env:OTEL_BSP_SCHEDULE_DELAY=5000  # Batch span processor delay (ms)
$env:OTEL_BSP_MAX_QUEUE_SIZE=2048  # Max queued spans
```

---

## Quick Commands

```powershell
# Install everything
pip install opentelemetry-distro opentelemetry-exporter-otlp opentelemetry-exporter-prometheus
opentelemetry-bootstrap -a install
winget install GrafanaLabs.Alloy

# Start Alloy
net start alloy

# Run MCP with instrumentation
$env:OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"
$env:OTEL_SERVICE_NAME="web-search-mcp"
opentelemetry-instrument python -m kindly_web_search_mcp_server.server

# Test metrics endpoint
curl http://localhost:9090/metrics

# Test OTLP endpoint
curl http://localhost:4318/v1/traces -X POST -d "{}" -H "Content-Type: application/x-protobuf"
```

---

## Windows Alloy Service Commands

```powershell
# Install
winget install GrafanaLabs.Alloy

# Start
net start alloy

# Stop
net stop alloy

# Status
sc query alloy

# Config location
$env:PROGRAMFILES\GrafanaLabs\Alloy\config.alloy

# Logs
Get-Content "$env:PROGRAMFILES\GrafanaLabs\Alloy\data\log\alloy.log"
```

---

## Summary

| Approach | Install | Config | Run |
|----------|---------|--------|-----|
| **1. opentelemetry-instrument** | pip + bootstrap | env vars | `opentelemetry-instrument python app.py` |
| **2. Prometheus + OTLP + Alloy** | pip + winget | config.alloy | Alloy service + instrumented app |
| **3. Full Alloy Collector** | pip + winget | config.alloy with enrichment | Production deployment |

**Start with Approach 1** for testing. **Move to Approach 2** for production reliability.