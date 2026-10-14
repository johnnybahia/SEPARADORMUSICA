@echo off
setlocal
cd /d "%~dp0"
title KaraokeSep - modo festa

if not exist "venv\Scripts\python.exe" (
  echo Ambiente nao encontrado. Rode instalar_player.bat ^(so tocar^)
  echo ou instalar.bat ^(programa completo^) primeiro.
  pause
  exit /b 1
)

if not exist "saida\festa.html" (
  echo Gerando a pagina da festa...
  "venv\Scripts\python.exe" -c "import app.festa as f; d,l=f.gerar(); print(len(l),'musicas na fila')"
  if errorlevel 1 (
    echo.
    echo Nao consegui gerar a pagina. Abra o programa pelo rodar.bat
    echo e use a aba Modo festa.
    pause
    exit /b 1
  )
)

echo.
echo ============================================
echo  Modo festa
echo ============================================
echo.
echo  O navegador vai abrir sozinho.
echo  MANTENHA ESTA JANELA ABERTA enquanto a festa rolar:
echo  e ela que serve os arquivos de audio.
echo.
echo  Para encerrar, feche esta janela ou pressione Ctrl+C.
echo.
echo  Lembre: o microfone NAO deve passar pelo PC. A latencia
echo  do Windows atrapalha quem canta. Ligue o microfone
echo  direto na caixa ou numa mesa.
echo.

"venv\Scripts\python.exe" -c "import webbrowser, time, threading, app.festa as f; f.gerar(); u=f.endereco(); print('  Endereco:', u); threading.Timer(1.0, lambda: webbrowser.open(u)).start(); [time.sleep(3600) for _ in iter(int, 1)]"

pause
