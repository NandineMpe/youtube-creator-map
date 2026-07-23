$ErrorActionPreference = "Stop"

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)]
    [scriptblock] $Command,
    [Parameter(Mandatory = $true)]
    [string] $Description
  )

  & $Command
  if ($LASTEXITCODE -ne 0) {
    throw "$Description failed with exit code $LASTEXITCODE"
  }
}

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$committedLock = Join-Path $workspaceRoot "pylock.toml"
$generatedLock = Join-Path $workspaceRoot "pylock.check.toml"

try {
  Invoke-Checked {
    python -m pip lock --group runtime --group dev --output $generatedLock
  } "Python lock generation"

  if ((Get-FileHash $committedLock).Hash -ne (Get-FileHash $generatedLock).Hash) {
    throw "pylock.toml is not synchronized with pyproject.toml or the target platform"
  }
}
finally {
  Remove-Item -Force -ErrorAction SilentlyContinue $generatedLock
}

Invoke-Checked { npm ci } "npm lock installation"
Invoke-Checked { npm run format:check } "Formatting"
Invoke-Checked { npm run lint } "Linting"
Invoke-Checked { npm run typecheck } "Type checking"
Invoke-Checked { npm test } "Unit tests"
Invoke-Checked { npm run build } "Static web build"
Invoke-Checked { python -m pip_audit --local } "Python vulnerability scan"
Invoke-Checked { npm audit --audit-level=high } "npm vulnerability scan"
