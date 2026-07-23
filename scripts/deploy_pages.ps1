# Build the site for GitHub Pages and push it to the gh-pages branch.
#
# The two-host split this project uses is deliberate and forced by the
# backends: Supabase Storage serves the JSON data and hashed JS/CSS
# correctly but refuses to serve HTML as text/html (an anti-XSS measure),
# so the HTML entry point lives on GitHub Pages instead. This script
# handles the Pages half; `publish.ps1` handles the Supabase half.
#
# The app is built with two independent prefixes:
#   NEXT_PUBLIC_BASE_PATH        — where the app's OWN assets are served
#                                  (the Pages project subpath)
#   NEXT_PUBLIC_ARTIFACT_BASE_URL — where the DATA is fetched from
#                                  (the Supabase public bucket)
#
# Usage:
#   pwsh scripts/deploy_pages.ps1
#   pwsh scripts/deploy_pages.ps1 -Repo NandineMpe/youtube-creator-map

param(
  [string] $Repo = "NandineMpe/youtube-creator-map",
  [string] $BasePath = "/youtube-creator-map",
  [string] $ArtifactBaseUrl = "https://ffipewnioaxjfhieqbaw.supabase.co/storage/v1/object/public/creator-map"
)

$ErrorActionPreference = "Stop"
$workspaceRoot = Split-Path -Parent $PSScriptRoot

Push-Location $workspaceRoot
try {
  Write-Host "=== Building for GitHub Pages ($BasePath)" -ForegroundColor Cyan
  $env:NEXT_PUBLIC_BASE_PATH = $BasePath
  $env:NEXT_PUBLIC_ARTIFACT_BASE_URL = $ArtifactBaseUrl

  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try { npm run build }
  finally { $ErrorActionPreference = $previous }
  if ($LASTEXITCODE -ne 0) { throw "web build failed" }

  # Pages runs Jekyll by default, which ignores files and folders starting
  # with an underscore — including Next's `_next` directory, which holds
  # every chunk. `.nojekyll` disables that so the assets are served.
  New-Item -ItemType File -Force -Path "apps/web/out/.nojekyll" | Out-Null

  Write-Host "=== Publishing apps/web/out to gh-pages" -ForegroundColor Cyan
  $publishDir = Join-Path $env:TEMP "creator-map-ghpages"
  if (Test-Path $publishDir) { Remove-Item -Recurse -Force $publishDir }
  New-Item -ItemType Directory -Path $publishDir | Out-Null
  Copy-Item -Recurse -Path "apps/web/out/*" -Destination $publishDir
  New-Item -ItemType File -Force -Path (Join-Path $publishDir ".nojekyll") | Out-Null

  Push-Location $publishDir
  try {
    git init -q
    git checkout -q -b gh-pages
    git add -A
    git -c user.email="deploy@local" -c user.name="deploy" commit -q -m "Deploy site to GitHub Pages"
    git remote add origin "https://github.com/$Repo.git"
    git push -q -f origin gh-pages
  }
  finally { Pop-Location }

  Write-Host ""
  Write-Host "Deployed. Pages will rebuild in a minute or two." -ForegroundColor Green
  $owner = $Repo.Split("/")[0].ToLower()
  $name = $Repo.Split("/")[1]
  Write-Host "  https://$owner.github.io/$name/"
}
finally {
  Pop-Location
}
