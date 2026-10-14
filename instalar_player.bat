@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title KaraokeSep - instalacao do player
color 07

set "LOG=%~dp0instalacao_player.log"
echo ============================================ > "%LOG%"
echo  KaraokeSep Player - log de instalacao >> "%LOG%"
echo  %DATE% %TIME% >> "%LOG%"
echo  Pasta: %CD% >> "%LOG%"
echo ============================================ >> "%LOG%"

echo ============================================
echo  KaraokeSep - instalacao so do PLAYER
echo ============================================
echo.
echo  Instala o necessario para TOCAR as musicas, com letra,
echo  partitura e mixer. Sao poucos MB: nao inclui o motor de
echo  separacao ^(PyTorch, modelos^), que roda no Google Colab.
echo.
echo  Tudo que acontecer aqui fica gravado em:
echo    instalacao_player.log
echo.

rem ==================================================================
rem  1 - ambiente
rem ==================================================================
echo [1/5] Conferindo o ambiente...

if /i not "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
  if /i not "%PROCESSOR_ARCHITEW6432%"=="AMD64" (
    echo.
    echo  ERRO: este PC nao e 64 bits ^(detectado: %PROCESSOR_ARCHITECTURE%^).
    echo  ERRO: arquitetura %PROCESSOR_ARCHITECTURE% >> "%LOG%"
    echo.
    pause
    exit /b 1
  )
)

echo %CD%| findstr /b "\\\\" >nul
if not errorlevel 1 (
  echo.
  echo  ERRO: o projeto esta numa pasta de rede ^(%CD%^).
  echo  O ambiente virtual do Python nao funciona em caminho de rede.
  echo  Copie para um disco local, por exemplo C:\KaraokeSep.
  echo  ERRO: caminho UNC >> "%LOG%"
  echo.
  pause
  exit /b 1
)

echo %CD%| findstr /i "OneDrive" >nul
if not errorlevel 1 (
  echo.
  echo  AVISO: a pasta esta dentro do OneDrive. A sincronizacao trava
  echo  arquivos em uso e sobe suas musicas para a nuvem sem necessidade.
  echo  O ideal e mover tudo para C:\KaraokeSep.
  echo  AVISO: dentro do OneDrive >> "%LOG%"
  echo.
  choice /c SN /t 20 /d N /m "Continuar assim mesmo"
  if errorlevel 2 (
    echo  Cancelado.
    pause
    exit /b 1
  )
)

echo teste > "%~dp0_escrita.tmp" 2>nul
if not exist "%~dp0_escrita.tmp" (
  echo.
  echo  ERRO: sem permissao para gravar nesta pasta.
  echo  Mova o projeto para uma pasta sua, como C:\KaraokeSep.
  echo  ERRO: sem permissao de escrita >> "%LOG%"
  echo.
  pause
  exit /b 1
)
del "%~dp0_escrita.tmp" >nul 2>&1

if not exist "requirements-player.txt" (
  echo.
  echo  ERRO: requirements-player.txt nao encontrado nesta pasta.
  echo  Descompacte o projeto inteiro e tente de novo.
  echo  ERRO: requirements-player.txt ausente >> "%LOG%"
  echo.
  pause
  exit /b 1
)
if not exist "app\festa.py" (
  echo.
  echo  ERRO: a pasta app nao foi encontrada.
  echo  Descompacte o zip inteiro, mantendo a estrutura de pastas.
  echo  ERRO: app\festa.py ausente >> "%LOG%"
  echo.
  pause
  exit /b 1
)
echo       ok
echo.

rem ==================================================================
rem  2 - Python
rem ==================================================================
echo [2/5] Procurando o Python ^(3.10 ou mais novo^)...
echo --- ETAPA 2: procura de Python --- >> "%LOG%"

set "PY="
set "VER="

where py >nul 2>&1
if not errorlevel 1 (
  for %%V in (3.12 3.13 3.11 3.10) do (
    if not defined PY (
      py -%%V -c "import sys" >nul 2>&1
      if not errorlevel 1 (
        set "PY=py -%%V"
        set "VER=%%V"
        echo encontrado pelo py launcher: %%V >> "%LOG%"
      )
    )
  )
)

if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 (
    python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1
    if not errorlevel 1 (
      set "PY=python"
      set "VER=do PATH"
      echo python do PATH aceito >> "%LOG%"
    )
  )
)

if not defined PY (
  for %%V in (312 313 311 310) do (
    if not defined PY (
      for %%P in (
        "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
        "%PROGRAMFILES%\Python%%V\python.exe"
        "C:\Python%%V\python.exe"
      ) do (
        if not defined PY if exist %%P (
          set "PY=%%P"
          set "VER=%%V"
          echo encontrado em disco: %%P >> "%LOG%"
        )
      )
    )
  )
)

if not defined PY (
  echo.
  echo  Nenhum Python compativel encontrado.
  echo nenhum Python encontrado >> "%LOG%"
  where winget >nul 2>&1
  if errorlevel 1 (
    echo.
    echo  O winget nao esta disponivel. Baixe o Python 3.12 em:
    echo    https://www.python.org/downloads/release/python-3129/
    echo  Escolha "Windows installer ^(64-bit^)" e marque a caixa
    echo  "Add python.exe to PATH" na primeira tela. Depois rode este
    echo  arquivo de novo.
    echo  ERRO: winget indisponivel >> "%LOG%"
    echo.
    pause
    exit /b 1
  )
  echo.
  echo  Posso instalar o Python 3.12 agora pelo winget ^(~30 MB^).
  echo  Pode aparecer uma janela de permissao do Windows.
  echo.
  choice /c SN /m "Instalar agora"
  if errorlevel 2 (
    echo  Cancelado.
    echo cancelado na instalacao do Python >> "%LOG%"
    pause
    exit /b 1
  )
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements >> "%LOG%" 2>&1
  py -3.12 -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    set "PY=py -3.12"
    set "VER=3.12"
  ) else (
    for %%P in (
      "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
      "%PROGRAMFILES%\Python312\python.exe"
    ) do (
      if not defined PY if exist %%P (
        set "PY=%%P"
        set "VER=3.12"
      )
    )
  )
  if not defined PY (
    echo.
    echo  O Python foi instalado, mas esta janela ainda nao o enxerga.
    echo  Feche esta janela e rode o instalar_player.bat de novo.
    echo  AVISO: Python instalado, PATH desatualizado >> "%LOG%"
    echo.
    pause
    exit /b 1
  )
)
echo       usando Python !VER!
echo usando: !PY! ^(!VER!^) >> "%LOG%"
echo.

rem ==================================================================
rem  3 - ambiente virtual
rem ==================================================================
echo [3/5] Criando o ambiente ^(pasta venv^)...
echo --- ETAPA 3: venv --- >> "%LOG%"

if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    echo       ja existe, reaproveitando
    echo venv existente reaproveitado >> "%LOG%"
    goto :venv_ok
  )
  echo       o venv que existe esta quebrado ^(copiado de outro PC?^)
  echo  Apague a pasta venv e rode este arquivo de novo.
  echo ERRO: venv quebrado >> "%LOG%"
  pause
  exit /b 1
)

!PY! -m venv venv >> "%LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo  ERRO: nao consegui criar o ambiente. Veja instalacao_player.log.
  echo ERRO: venv falhou >> "%LOG%"
  echo.
  pause
  exit /b 1
)
echo       ok
:venv_ok
echo.

rem ==================================================================
rem  4 - bibliotecas
rem ==================================================================
echo [4/5] Instalando as bibliotecas ^(poucos MB, de 1 a 2 minutos^)...
echo --- ETAPA 4: pip --- >> "%LOG%"

"venv\Scripts\python.exe" -m pip install --upgrade pip >> "%LOG%" 2>&1
"venv\Scripts\python.exe" -m pip install -r requirements-player.txt >> "%LOG%" 2>&1
if errorlevel 1 (
  echo       falhou na primeira tentativa, tentando de novo...
  "venv\Scripts\python.exe" -m pip install -r requirements-player.txt >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo.
    echo  ERRO: nao consegui instalar as bibliotecas.
    echo  Confira a internet e veja instalacao_player.log.
    echo ERRO: pip falhou >> "%LOG%"
    echo.
    pause
    exit /b 1
  )
)
echo       ok
echo.

rem ==================================================================
rem  5 - conferir e finalizar
rem ==================================================================
echo [5/5] Conferindo...
echo --- ETAPA 5: conferencia --- >> "%LOG%"

"venv\Scripts\python.exe" -c "import numpy, soundfile, requests, mutagen; import app.festa; print('ok')" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo  ERRO: o player nao abriu depois da instalacao.
  echo  Veja o fim do arquivo instalacao_player.log.
  echo ERRO: conferencia falhou >> "%LOG%"
  echo.
  pause
  exit /b 1
)

echo player> "modo_player.txt"
if not exist "saida" mkdir "saida"
echo       ok
echo.
echo ============================================
echo  Pronto. O player esta instalado.
echo ============================================
echo.
echo  COMO USAR:
echo   1. Separe as musicas no Google Colab ^(KaraokeSep_Colab.ipynb^).
echo   2. Abra o festa.bat.
echo   3. Na aba Importar, arraste o zip que o Colab baixou.
echo.
echo  Para atualizar o programa depois, use o botao "Atualizar app"
echo  no topo do player.
echo.

choice /c SN /m "Criar um atalho 'KaraokeSep Festa' na area de trabalho"
if not errorlevel 2 (
  powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\KaraokeSep Festa.lnk'); $s.TargetPath='%~dp0festa.bat'; $s.WorkingDirectory='%~dp0'; $s.Save()" >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo  Nao consegui criar o atalho. Use o festa.bat direto.
  ) else (
    echo  Atalho criado.
  )
)
echo.
choice /c SN /m "Abrir o player agora"
if not errorlevel 2 (
  call "%~dp0festa.bat"
)
endlocal
