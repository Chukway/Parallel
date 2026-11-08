# Builds dist\Parallel\Parallel.exe (onedir, windowed) directly with PyInstaller. Does not run the upstream release_materials gate.
param([string]$Venv = $(if ($env:PARALLEL_VENV) { $env:PARALLEL_VENV } else { Join-Path $PSScriptRoot ".venv" }))
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$py = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) { throw "Python venv not found: $py" }
foreach ($d in "build", "dist") {
    $p = Join-Path $PSScriptRoot $d
    if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force }
}
$ErrorActionPreference = "Continue"   # PyInstaller writes progress to stderr, which is not an error
& $py -m PyInstaller Parallel.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
Write-Host "Built: $PSScriptRoot\dist\Parallel\Parallel.exe"
# The Settings > OCR "Install / Repair" buttons read these lists from beside the exe.
foreach ($f in "requirements-rapidocr-test.txt", "requirements-paddleocr-test.txt", "requirements-windowsocr.txt") {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $f) -Destination (Join-Path $PSScriptRoot "dist\Parallel\$f") -Force
}
