@echo off
rem Optional local English speech recognition. Existing .env and saved games stay intact.
cd /d "%~dp0" || goto :fail
if not exist "backend\.venv\Scripts\python.exe" (
  echo Run setup.cmd first, then run setup-speech.cmd.
  pause
  exit /b 1
)
"backend\.venv\Scripts\python.exe" -m pip install -r backend\requirements-speech.txt || goto :fail
"backend\.venv\Scripts\python.exe" backend\tools\prepare_speech.py %* || goto :fail
echo.
echo The selected local speech model is ready. Restart the game server if it is already running.
pause
exit /b 0
:fail
echo.
echo Speech setup did not finish. See the message above; typed play still works.
pause
exit /b 1
