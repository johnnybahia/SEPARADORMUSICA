@echo off
cd /d "%~dp0"
title KaraokeSep - catalogo de modelos
if not exist "venv\Scripts\python.exe" (
  echo Ambiente nao encontrado. Rode instalar.bat primeiro.
  pause
  exit /b 1
)
"venv\Scripts\python.exe" -m app.modelos > catalogo_modelos.txt 2>&1
echo Catalogo gravado em catalogo_modelos.txt
type catalogo_modelos.txt
pause
