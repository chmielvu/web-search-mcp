# kindly-cli.ps1 ? PowerShell wrapper that calls mcp2cli against the local Kindly Web Search MCP server over stdio.
#
# Usage (from any cwd):
#   .\scripts\kindly-cli.ps1 --list
#   .\scripts\kindly-cli.ps1 web-search --query "what is x" --research-goal "..."
#   .\scripts\kindly-cli.ps1 get-content --url "https://..."
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Mcp2CliArgs
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$repoRoot  = Resolve-Path (Join-Path $scriptDir '..')

if (-not $env:KINDLY_VENV_PYTHON) {
    $candidateWin = Join-Path $repoRoot '.venv\Scripts\python.exe'
    $candidatePosix = Join-Path $repoRoot '.venv/bin/python'
    if (Test-Path -LiteralPath $candidateWin) {
        $env:KINDLY_VENV_PYTHON = (Resolve-Path -LiteralPath $candidateWin).Path
    } elseif (Test-Path -LiteralPath $candidatePosix) {
        $env:KINDLY_VENV_PYTHON = (Resolve-Path -LiteralPath $candidatePosix).Path
    } else {
        Write-Error 'kindly-cli: cannot find venv python (set KINDLY_VENV_PYTHON)'
        exit 2
    }
}

if (-not $env:KINDLY_CLI_MCP2CLI) { $env:KINDLY_CLI_MCP2CLI = 'uvx mcp2cli' }

# Place the venv Scripts dir on PATH so uvx etc. resolve from the project venv.
$scriptsDir = Split-Path -Parent $env:KINDLY_VENV_PYTHON
if ($env:PATH -notlike "*$scriptsDir*") { $env:PATH = "$scriptsDir;$env:PATH" }

# On Windows, mcp2cli's stdio backend cannot directly exec a .bat ? wrap with cmd /c.
$launcherBat = Join-Path $scriptDir 'kindly-mcp-stdio.bat'
$stdioCmd = "cmd /c `"$launcherBat`""

Push-Location $repoRoot
try {
    # Split KINDLY_CLI_MCP2CLI on whitespace (e.g. "uvx mcp2cli" -> @('uvx','mcp2cli'))
    $mcp2cliCmd = $env:KINDLY_CLI_MCP2CLI -split '\s+'
    $argList = @('--mcp-stdio', $stdioCmd) + $Mcp2CliArgs
    & $mcp2cliCmd[0] @($mcp2cliCmd[1..($mcp2cliCmd.Length - 1)] + $argList)
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
