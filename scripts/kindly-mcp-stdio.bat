@echo off
rem Launcher for Kindly Web Search MCP server over stdio.
rem Picked up by mcp2cli --mcp-stdio so the agent never needs to know the venv path.

setlocal
set "PROJECT_ROOT=%~dp0..\.."
pushd "%PROJECT_ROOT%"
"C:\Users\Jan\Documents\GitHub\1Agents1\.CLI\web-search-mcp\.venv\Scripts\python.exe" -m kindly_web_search_mcp_server --transport stdio
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
