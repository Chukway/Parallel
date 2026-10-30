# Builds dist\Parallel\Parallel.exe (onedir, windowed) directly with PyInstaller. Does not run the upstream release_materials gate.
param([string]$Venv = "S:\.venv-sidro")
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$py = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) { throw "Python venv not found: $py" }
foreach ($d in "build", "dist") {
    $p = Join-Path $PSScriptRoot $d
    if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force }
}
& $py -m PyInstaller Parallel.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
Write-Host "Built: $PSScriptRoot\dist\Parallel\Parallel.exe"
