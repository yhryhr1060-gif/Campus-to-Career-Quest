@echo off
rem One-time setup. Double-click, or run from cmd / PowerShell: .\setup.cmd
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
  echo [1/3] Creating Python virtual environment in backend\.venv ...
  py -3.11 -m venv backend\.venv 2>nul || py -m venv backend\.venv
)
echo [1/3] Installing backend packages into backend\.venv (not into your global Python) ...
"backend\.venv\Scripts\python.exe" -m pip install --upgrade pip >nul
"backend\.venv\Scripts\python.exe" -m pip install -r backend\requirements.txt || goto :fail

if not exist ".env" (
  echo [2/3] Creating .env from .env.example ^(mock mode, no API key needed^)
  copy ".env.example" ".env" >nul
) else (
  echo [2/3] .env already exists, keeping it
)

echo [3/3] Installing frontend packages ...
pushd frontend
call npm.cmd ci || (popd & goto :fail)
popd

echo.
echo Setup finished. Next: double-click run.cmd
pause
exit /b 0

:fail
echo.
echo Setup FAILED - see the message above.
pause
exit /b 1
