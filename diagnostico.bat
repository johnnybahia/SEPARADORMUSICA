@echo off
cd /d "%~dp0"
title KaraokeSep - diagnostico
if not exist "venv\Scripts\python.exe" (
  echo Ambiente nao encontrado. Rode instalar.bat primeiro.
  pause
  exit /b 1
)
"venv\Scripts\python.exe" -m app.diagnostico > diagnostico.txt 2>&1
type diagnostico.txt
echo.
echo ============================================
echo  Relatorio salvo em diagnostico.txt
echo ============================================
echo.
pause
