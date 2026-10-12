param(
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
$providerRoot = $PSScriptRoot
$desktopRoot = Join-Path $providerRoot "desktop"
Push-Location (Join-Path $providerRoot "ui")
try {
    & bun run build
    if ($LASTEXITCODE -ne 0) { throw "Provider UI build failed" }
} finally { Pop-Location }
Push-Location $providerRoot
try {
    & $Python -m PyInstaller --noconfirm --distpath (Join-Path $desktopRoot "bundle") --workpath (Join-Path $providerRoot "build") provider.spec
    if ($LASTEXITCODE -ne 0) { throw "Provider executable build failed" }
} finally { Pop-Location }
Push-Location $desktopRoot
try {
    & npm run dist
    if ($LASTEXITCODE -ne 0) { throw "Provider desktop package failed" }
} finally { Pop-Location }
