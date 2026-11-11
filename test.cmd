@echo off
rem Runs the backend test suite (pytest) inside backend\.venv
cd /d "%~dp0backend"
set CCQ_SKIP_DOTENV=1
set LLM_MOCK=1
".venv\Scripts\python.exe" -m pytest -q
pause
