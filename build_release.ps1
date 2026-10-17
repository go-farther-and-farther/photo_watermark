# Build release: read VERSION from photo_watermark.py, run PyInstaller, then
# copy the artifact to a versioned filename (dist\photo_watermark_v2.1.0.exe)
# so it can be shared directly.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File build_release.ps1
#   ... -Python "C:\path\to\python.exe"   # use a specific interpreter
#   ... -SkipBuild                        # only rename the existing artifact
#   ... -NoRootSync                       # do not touch .\photo_watermark.exe
param(
    [string]$Python = 'python',
    [switch]$SkipBuild,
    [switch]$NoRootSync
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$m = Select-String -Path 'photo_watermark.py' -Pattern "^VERSION = '([^']+)'" | Select-Object -First 1
if (-not $m) { throw 'VERSION not found in photo_watermark.py' }
$ver = $m.Matches[0].Groups[1].Value        # e.g. v2.1.0
Write-Host "[release] version $ver"

if (-not $SkipBuild) {
    & $Python -m PyInstaller photo_watermark.spec -y --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }
}

$src = Join-Path $PSScriptRoot 'dist\photo_watermark.exe'
if (-not (Test-Path $src)) { throw "missing $src" }
$dst = Join-Path $PSScriptRoot "dist\photo_watermark_$ver.exe"
Copy-Item $src $dst -Force
$mb = [math]::Round((Get-Item $dst).Length / 1MB, 1)
Write-Host "[release] artifact: $dst ($mb MB)"

if (-not $NoRootSync) {
    $root = Join-Path $PSScriptRoot 'photo_watermark.exe'
    try {
        Copy-Item $src $root -Force
        Write-Host "[release] synced .\photo_watermark.exe"
    } catch {
        Write-Host "[release] root exe is locked (app running?); close it and rerun with -SkipBuild"
    }
}
