@echo off
rem Runs the prompt regression tests against the model set in .env (3 runs per case).
rem Results: this window and data\logs\llm_check.log
cd /d "%~dp0.."
"backend\.venv\Scripts\python.exe" backend\tools\prompt_regression.py --runs 3
echo.
pause
