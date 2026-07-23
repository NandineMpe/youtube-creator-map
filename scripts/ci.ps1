$ErrorActionPreference = "Stop"

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)]
    [scriptblock] $Command,
    [Parameter(Mandatory = $true)]
    [string] $Description
  )

  # Windows PowerShell 5.1 turns a native command's stderr into an
  # ErrorRecord, which $ErrorActionPreference = "Stop" then treats as
  # fatal. A tool that writes progress or a deprecation notice to stderr
  # would fail the build while exiting 0. Exit code is the only reliable
  # signal for a native command.
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $Command
  }
  finally {
    $ErrorActionPreference = $previous
  }

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
# The checked-in fixture must match what the generator produces. If it
# drifted, the cross-stack contract test is validating stale bytes.
Invoke-Checked {
  python -m creator_map_pipeline.cli_fixture --out fixtures/dist --activate
} "Synthetic fixture generation"

$fixtureDrift = git status --porcelain -- fixtures/dist
if ($fixtureDrift) {
  throw "fixtures/dist is out of date. Run 'python -m creator_map_pipeline.cli_fixture --out fixtures/dist --activate' and commit the result."
}

Invoke-Checked { npm run format:check } "Formatting"
Invoke-Checked { npm run lint } "Linting"
Invoke-Checked { npm run typecheck } "Type checking"
Invoke-Checked { npm test } "Unit tests"
Invoke-Checked { npm run build } "Static web build"
Invoke-Checked { python -m pip_audit --local --skip-editable } "Python vulnerability scan"
Invoke-Checked { npm audit --audit-level=high } "npm vulnerability scan"
