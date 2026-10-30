# Creates a "Parallel" shortcut. Run on demand; nothing is installed automatically.
#   .\tools\make_shortcut.ps1                 -> shortcut on the Desktop
#   .\tools\make_shortcut.ps1 -StartMenu      -> shortcut in the current user's Start menu
#   .\tools\make_shortcut.ps1 -Exe D:\Apps\Parallel\Parallel.exe
param(
    [string]$Exe = (Join-Path (Split-Path -Parent $PSScriptRoot) "dist\Parallel\Parallel.exe"),
    [switch]$StartMenu
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Exe)) { throw "Parallel.exe not found: $Exe" }
$Exe = (Resolve-Path -LiteralPath $Exe).Path
$folder = if ($StartMenu) { [Environment]::GetFolderPath("Programs") } else { [Environment]::GetFolderPath("Desktop") }
$link = Join-Path $folder "Parallel.lnk"
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($link)
$sc.TargetPath = $Exe
$sc.WorkingDirectory = Split-Path -Parent $Exe
$sc.IconLocation = "$Exe,0"
$sc.Description = "Parallel"
$sc.Save()
Write-Host "Created $link -> $Exe"
