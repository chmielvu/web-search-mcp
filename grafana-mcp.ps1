# Grafana MCP Server Launcher
# Requires GRAFANA_URL and GRAFANA_SERVICE_ACCOUNT_TOKEN to be set

if (-not $env:GRAFANA_URL) {
    Write-Error "GRAFANA_URL environment variable not set"
    exit 1
}
if (-not $env:GRAFANA_SERVICE_ACCOUNT_TOKEN) {
    Write-Error "GRAFANA_SERVICE_ACCOUNT_TOKEN environment variable not set"
    exit 1
}
uvx mcp-grafana
