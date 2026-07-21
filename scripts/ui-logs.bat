@echo off
REM Launch DuckDB Local UI for the process logs database
set DB_PATH=%~dp0..\duckdb_data\logs\process_logs.duckdb
if not exist "%DB_PATH%" (
    echo Process logs DB not found at %DB_PATH%
    pause
    exit /b 1
)
echo Opening DuckDB UI for process logs...
start duckdb -ui "%DB_PATH%"
