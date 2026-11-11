@echo off
rem Backend window. Python itself writes the log to data\logs\backend.log
cd /d "%~dp0..\backend"
set PYTHONUNBUFFERED=1
echo Starting backend on http://127.0.0.1:8000  (log: data\logs\backend.log)
echo Keep this window open while you play.
echo.
".venv\Scripts\python.exe" serve.py
echo.
echo Backend stopped (exit code %errorlevel%). See the messages above.
