# Build a complete, inactive release: artifacts, web bundle, gates, plan.
#
# Requirement 8.4 wants the whole versioned artifact set staged before
# anything changes the active pointer, so this script deliberately stops
# short of activation. It ends with a validated release sitting on disk
# and a publication plan describing how to upload it. Making it active is
# a separate, explicit operator command:
#
#   python -m creator_map_pipeline.cli_release activate --dir dist --actor <you>
#
# Splitting it this way means a broken release is a build that failed,
# not an outage.
#
# Usage:
#   pwsh scripts/build_release.ps1 -Actor nandi
#   pwsh scripts/build_release.ps1 -Actor nandi -SkipWeb

param(
  [Parameter(Mandatory = $true)]
  [string] $Actor,

  [string] $Out = "dist",

  # Read from the environment by default. A secret passed on the command
  # line lands in shell history and process listings; Requirement 15.1
  # wants it out of both.
  [string] $PublicKeySecret = $env:PUBLIC_KEY_SECRET,

  [string] $PolicyVersion = "1.0.0",
  [string] $ChannelPolicyVersion = "1.0.0-country",
  [int] $PageSize = 50,

  [switch] $SkipWeb
)

$ErrorActionPreference = "Stop"

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)] [scriptblock] $Command,
    [Parameter(Mandatory = $true)] [string] $Description
  )
  Write-Host ""
  Write-Host "=== $Description" -ForegroundColor Cyan

  # Windows PowerShell 5.1 wraps a native executable's stderr in an
  # ErrorRecord, so with $ErrorActionPreference = "Stop" a routine
  # progress line on stderr aborts the script as though the command had
  # failed. The pipeline writes its connection target there, which made
  # every successful build look like a failure. Exit code is the only
  # trustworthy signal for a native command, so this suspends the
  # preference around the call and checks $LASTEXITCODE instead.
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

if ([string]::IsNullOrWhiteSpace($PublicKeySecret)) {
  throw "PUBLIC_KEY_SECRET is not set. Public channel keys are HMAC-derived; without the secret a plain digest of a 24-character channel id is reversible by enumeration."
}

$workspaceRoot = Split-Path -Parent $PSScriptRoot
Push-Location $workspaceRoot
try {
  # 1. Aggregate and generate artifacts. Writes only to $Out.
  Invoke-Checked {
    python -m creator_map_pipeline.cli_build `
      --out $Out `
      --actor $Actor `
      --policy-version $PolicyVersion `
      --channel-policy-version $ChannelPolicyVersion `
      --page-size $PageSize `
      --public-key-secret $PublicKeySecret
  } "Build release artifacts"

  # 2. Static export. No runtime API: the app reads published JSON only.
  if (-not $SkipWeb) {
    Invoke-Checked { npm run build } "Static web export"
  }

  # 3. Every gate, without activating. A failure here leaves the
  #    previously active release untouched, which is the point.
  #
  #    A freshly built release has no sign-off yet, and the sign-off gate
  #    is scoped to this manifest digest, so it will report INCOMPLETE.
  #    That is the expected state of an unreviewed build, not a defect,
  #    so this reports the gate results and keeps going rather than
  #    aborting with a stack trace. Activation is what must not proceed,
  #    and it is a separate command that runs the same gates itself.
  Write-Host ""
  Write-Host "=== Release gates (no activation)" -ForegroundColor Cyan
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    python -m creator_map_pipeline.cli_release validate --dir $Out --actor $Actor
  }
  finally {
    $ErrorActionPreference = $previous
  }
  $gatesPassed = ($LASTEXITCODE -eq 0)

  # 4. The publication plan. Pure filesystem, no storage credential.
  $webArg = if ($SkipWeb) { @() } else { @("--web", "apps/web/out") }
  Invoke-Checked {
    python -m creator_map_pipeline.cli_deliver --dir $Out --json "$Out/delivery-plan.json" @webArg
  } "Delivery plan"

  # 5. Delivery budgets. Reports what it can measure and says plainly
  #    that LCP is not among them.
  if (-not $SkipWeb) {
    Invoke-Checked {
      python scripts/measure_performance.py $Out apps/web/out
    } "Delivery budgets"
  }

  Write-Host ""
  if ($gatesPassed) {
    Write-Host "Release built and every gate passed. It is NOT active." -ForegroundColor Yellow
    Write-Host "To activate:" -ForegroundColor Yellow
    Write-Host "  python -m creator_map_pipeline.cli_release activate --dir $Out --actor $Actor --scan-completed"
  }
  else {
    Write-Host "Release built. One or more gates did not pass; see above." -ForegroundColor Yellow
    Write-Host "A fresh build has no sign-off yet, which is the usual reason." -ForegroundColor Yellow
    Write-Host "Review the artifacts, then record approval of these exact bytes:" -ForegroundColor Yellow
    Write-Host "  python -m creator_map_pipeline.cli_release signoff --dir $Out --actor <curator> --citations --terms"
    Write-Host ""
    Write-Host "The currently active release is unchanged." -ForegroundColor Yellow
  }
}
finally {
  Pop-Location
}
