@echo off
setlocal
cd /d "%~dp0.."
set "PARALLEL_PYTHON=.venv-parallel\Scripts\python.exe"
if not exist "%PARALLEL_PYTHON%" (
  echo Run setup_environment.ps1 before starting Parallel from source.
  exit /b 1
)
"%PARALLEL_PYTHON%" -B tools\check_dependencies.py
if errorlevel 1 exit /b 1
"%PARALLEL_PYTHON%" %*
exit /b %errorlevel%
