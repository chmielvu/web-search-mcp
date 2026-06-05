@echo off
rem kindly-cli.cmd ? Windows cmd wrapper that calls mcp2cli against the local Kindly Web Search MCP server over stdio.
rem
rem Usage (from any cwd):
rem   scripts\kindly-cli.cmd --list
rem   scripts\kindly-cli.cmd web-search --query "what is x" --research-goal "..."
rem   scripts\kindly-cli.cmd get-content --url "https://..."

setlocal
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%\.."

if not defined KINDLY_VENV_PYTHON (
  if exist ".venv\Scripts\python.exe" (
    set "KINDLY_VENV_PYTHON=%CD%\.venv\Scripts\python.exe"
  ) else if exist ".venv\bin\python" (
    set "KINDLY_VENV_PYTHON=%CD%\.venv\bin\python"
  ) else (
    echo kindly-cli: cannot find venv python ^(set KINDLY_VENV_PYTHON^) 1>&2
    popd
    exit /b 2
  )
)

if not defined KINDLY_CLI_MCP2CLI set "KINDLY_CLI_MCP2CLI=uvx mcp2cli"

rem Place the venv Scripts dir on PATH so uvx etc. resolve from the project venv.
set "PATH=%~dp0..\.venv\Scripts;%PATH%"

rem On Windows, mcp2cli's stdio backend cannot directly exec a .bat ? wrap with cmd /c.
set "LAUNCHER_BAT=%SCRIPT_DIR%kindly-mcp-stdio.bat"
set "STDIO_CMD=cmd /c ""%LAUNCHER_BAT%"""

call %KINDLY_CLI_MCP2CLI% --mcp-stdio "%STDIO_CMD%" %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
