@echo off
REM Launch DuckDB Local UI for the page cache database
set DB_PATH=%~dp0..\duckdb_data\cache\page_cache.duckdb
if not exist "%DB_PATH%" (
    echo Cache DB not found at %DB_PATH%
    pause
    exit /b 1
)
echo Opening DuckDB UI for page cache...
start duckdb -ui "%DB_PATH%"
