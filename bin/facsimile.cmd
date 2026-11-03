@echo off
rem Windows shim so `facsimile` also works from cmd and PowerShell.
rem Uses %FACSIMILE_PYTHON% if set, else the first of python / py -3 that actually runs.
setlocal
set "FX=%~dp0..\scripts\facsimile.py"
if defined FACSIMILE_PYTHON (
  "%FACSIMILE_PYTHON%" "%FX%" %*
  exit /b %errorlevel%
)
python -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>nul
if %errorlevel% neq 0 goto trypy
python "%FX%" %*
exit /b %errorlevel%
:trypy
py -3 "%FX%" %*
exit /b %errorlevel%
