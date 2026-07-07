@echo off
REM Остановить Slides App: завершает процесс, слушающий порт приложения (по умолчанию 8000).
setlocal
if "%SLIDES_PORT%"=="" (set PORT=8000) else (set PORT=%SLIDES_PORT%)

set FOUND=
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  echo Останавливаю Slides App (PID %%P)...
  taskkill /PID %%P /F >nul 2>&1
  set FOUND=1
)

if not defined FOUND echo Slides App не запущен (порт %PORT% свободен).
endlocal
