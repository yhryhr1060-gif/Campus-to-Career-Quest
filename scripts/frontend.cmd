@echo off
rem Frontend window: runs the Vite dev server and also writes the output to data\logs\frontend.log
cd /d "%~dp0..\frontend"
if not exist "..\data\logs" mkdir "..\data\logs"
echo Starting frontend on http://127.0.0.1:5173  (log: data\logs\frontend.log)
node --version 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '..\data\logs\frontend.log'"
call npm.cmd run dev 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '..\data\logs\frontend.log' -Append"
echo.
echo Frontend stopped. See the messages above.
