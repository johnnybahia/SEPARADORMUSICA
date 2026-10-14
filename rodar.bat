@echo off
cd /d "%~dp0"
title KaraokeSep
if not exist "venv\Scripts\python.exe" (
  echo Ambiente nao encontrado. Rode instalar.bat primeiro.
  pause
  exit /b 1
)
"venv\Scripts\python.exe" -m app.main
if errorlevel 1 pause
