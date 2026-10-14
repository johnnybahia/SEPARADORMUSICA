@echo off
cd /d "%~dp0"
title KaraokeSep - faxina
if not exist "venv\Scripts\python.exe" (
  echo Ambiente nao encontrado. Rode instalar.bat primeiro.
  pause
  exit /b 1
)
"venv\Scripts\python.exe" -m app.faxina
echo.
pause
