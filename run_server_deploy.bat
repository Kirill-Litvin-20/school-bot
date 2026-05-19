@echo off
setlocal

REM Быстрый деплой на сервер одной командой.
set SERVER_HOST=151.243.176.132
set SERVER_USER=root

echo [INFO] Deploy to %SERVER_USER%@%SERVER_HOST% ...
ssh %SERVER_USER%@%SERVER_HOST% "bash /opt/school-system/deploy.sh"

if errorlevel 1 (
  echo [ERROR] Deploy failed.
  exit /b 1
)

echo [OK] Deploy finished.
endlocal
