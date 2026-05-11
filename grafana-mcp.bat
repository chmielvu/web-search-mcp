@echo off
REM Grafana MCP Server Launcher
REM Requires GRAFANA_URL and GRAFANA_SERVICE_ACCOUNT_TOKEN to be set
if "%GRAFANA_URL%"=="" (
    echo Error: GRAFANA_URL environment variable not set
    exit /b 1
)
if "%GRAFANA_SERVICE_ACCOUNT_TOKEN%"=="" (
    echo Error: GRAFANA_SERVICE_ACCOUNT_TOKEN environment variable not set
    exit /b 1
)
uvx mcp-grafana
