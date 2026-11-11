@echo off
setlocal
cd /d "%~dp0.."
set "CCQ_SKIP_DOTENV=1"
set "LLM_MOCK=1"
set "LLM_API_KEY="
set "LLM_BASE_URL=http://127.0.0.1:1"
if not exist "backend\.venv\Scripts\python.exe" (
  echo The project's Python environment is missing.
  exit /b 1
)
pushd frontend
call npm.cmd run build
if errorlevel 1 (
  popd
  exit /b 1
)
popd
echo Open http://127.0.0.1:18746 for the game.
echo Open http://127.0.0.1:18746/api/__test for mock fault controls.
echo Press Ctrl+C to stop. Each launch keeps a separate synthetic save folder.
"backend\.venv\Scripts\python.exe" "backend\tools\round6_acceptance_server.py"
endlocal
