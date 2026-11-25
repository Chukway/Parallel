param([Parameter(Mandatory)][string]$Script, [string[]]$Arguments = @())
$ErrorActionPreference = "Stop"
$sourceRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $sourceRoot
$sourcePython = Join-Path $sourceRoot ".venv-parallel\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $sourcePython)) {
    throw "Run setup_environment.ps1 before starting Parallel from source."
}
& $sourcePython -B "$PSScriptRoot\check_dependencies.py"
if ($LASTEXITCODE -ne 0) { throw "Parallel dependency baseline differs from the lock." }
& $sourcePython (Join-Path $sourceRoot $Script) @Arguments
if ($LASTEXITCODE -ne 0) { throw "Parallel exited with code $LASTEXITCODE." }
