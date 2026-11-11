@echo off
rem Starts the backend and the frontend in two windows, waits until the game is up, then opens it.
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" (
  echo Run setup.cmd first.
  pause
  exit /b 1
)
start "CCQ backend :8000" cmd /k ""%~dp0scripts\backend.cmd""
start "CCQ frontend :5173" cmd /k ""%~dp0scripts\frontend.cmd""

echo Waiting for the game to start ...
set /a tries=0
:wait
set /a tries+=1
curl.exe -s -o nul http://127.0.0.1:5173/ && goto open
if %tries% geq 60 goto timeout
timeout /t 1 /nobreak >nul
goto wait

:open
echo Opening http://127.0.0.1:5173
start "" http://127.0.0.1:5173
exit /b 0

:timeout
echo The frontend did not start within 60 seconds.
echo Look at the "CCQ frontend" window, or data\logs\frontend.log
pause
