# Build the web bundle for the CDN and publish a release to Supabase Storage.
#
# This is the last mile: a release that has already passed every gate and
# been activated in the database, pushed to the object store the browser
# actually reads from. It does not re-run the gates — `build_release.ps1`
# and `cli_release accept` do that — it takes verified output and puts it
# where visitors can reach it.
#
# The one credential it needs, the service role key, is read from the
# environment and never from an argument, because an argument lands in
# shell history and the process list (Requirement 15.1).
#
# Usage:
#   $env:SUPABASE_SERVICE_KEY = '<service_role key>'
#   pwsh scripts/publish.ps1
#
#   # Stage without moving the pointer, to review before going live:
#   pwsh scripts/publish.ps1 -StageOnly

param(
  [string] $Dir = "dist",
  [string] $WebOut = "apps/web/out",
  [switch] $StageOnly,
  [switch] $SkipWebBuild
)

$ErrorActionPreference = "Stop"

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)] [scriptblock] $Command,
    [Parameter(Mandatory = $true)] [string] $Description
  )
  Write-Host ""
  Write-Host "=== $Description" -ForegroundColor Cyan

  # Windows PowerShell 5.1 wraps a native command's stderr in an
  # ErrorRecord, which $ErrorActionPreference = "Stop" then treats as
  # fatal. Exit code is the only trustworthy signal for a native command.
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try { & $Command }
  finally { $ErrorActionPreference = $previous }

  if ($LASTEXITCODE -ne 0) {
    throw "$Description failed with exit code $LASTEXITCODE"
  }
}

# Load .env.local so SUPABASE_URL and the public base URL do not have to be
# set by hand every time. The service key is deliberately absent from that
# file and must come from the shell.
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $workspaceRoot ".env.local"
if (Test-Path $envFile) {
  foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') {
      $name = $Matches[1]
      $value = $Matches[2].Trim().Trim('"').Trim("'")
      # Do not clobber a value already set in the session — the shell wins,
      # which is how the service key gets in.
      if (-not (Get-Item -Path "Env:$name" -ErrorAction SilentlyContinue)) {
        Set-Item -Path "Env:$name" -Value $value
      }
    }
  }
}

if ([string]::IsNullOrWhiteSpace($env:SUPABASE_SERVICE_KEY)) {
  throw @"
SUPABASE_SERVICE_KEY is not set.

The publishable key cannot write to storage by design, so publishing needs
the service role key from Supabase: Project Settings > API > service_role.

It is a full-access credential. Set it for this session only:
  `$env:SUPABASE_SERVICE_KEY = '<service_role key>'
and do not commit it or store it in .env.local.
"@
}

Push-Location $workspaceRoot
try {
  # The bundle must be built with the artifact base URL baked in, or its
  # fetches resolve against the wrong origin and the CSP blocks them.
  if (-not $SkipWebBuild) {
    if ([string]::IsNullOrWhiteSpace($env:NEXT_PUBLIC_ARTIFACT_BASE_URL)) {
      throw "NEXT_PUBLIC_ARTIFACT_BASE_URL is not set; the web build needs it to fetch artifacts from the CDN."
    }
    Invoke-Checked { npm run build } "Build web bundle for the CDN"
  }

  $publishArgs = @("--dir", $Dir, "--web", $WebOut)
  if ($StageOnly) { $publishArgs += "--stage-only" }

  Invoke-Checked {
    python -m creator_map_pipeline.cli_publish @publishArgs
  } "Publish to Supabase Storage"

  Write-Host ""
  if ($StageOnly) {
    Write-Host "Staged. The pointer was not moved; the live release is unchanged." -ForegroundColor Yellow
    Write-Host "To go live, re-run without -StageOnly." -ForegroundColor Yellow
  }
  else {
    Write-Host "Published and live." -ForegroundColor Green
  }
}
finally {
  Pop-Location
}
