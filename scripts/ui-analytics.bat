@echo off
REM Launch DuckDB Local UI for the analytics database
set DB_PATH=%~dp0..\duckdb_data\analytics\search_events.duckdb
if not exist "%DB_PATH%" (
    echo Analytics DB not found at %DB_PATH%
    echo Run web-search-cli first to generate data.
    pause
    exit /b 1
)
echo Opening DuckDB UI for analytics...
start duckdb -ui "%DB_PATH%"
