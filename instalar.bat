@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title KaraokeSep - instalacao
color 07

set "LOG=%~dp0instalacao.log"
echo ============================================ > "%LOG%"
echo  KaraokeSep - log de instalacao >> "%LOG%"
echo  %DATE% %TIME% >> "%LOG%"
echo  Pasta: %CD% >> "%LOG%"
echo ============================================ >> "%LOG%"

echo ============================================
echo  KaraokeSep - instalacao
echo ============================================
echo.
echo  Tudo que acontecer aqui fica gravado em:
echo    instalacao.log
echo.

rem ==================================================================
rem  ETAPA 0 - ambiente
rem ==================================================================
echo [0/7] Conferindo o ambiente...

rem --- arquitetura ---
if /i not "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
  if /i not "%PROCESSOR_ARCHITEW6432%"=="AMD64" (
    echo.
    echo  ERRO: este PC nao e 64 bits ^(detectado: %PROCESSOR_ARCHITECTURE%^).
    echo  O PyTorch nao tem versao para 32 bits. Nao ha como seguir.
    echo  ERRO: arquitetura %PROCESSOR_ARCHITECTURE% >> "%LOG%"
    echo.
    pause
    exit /b 1
  )
)

rem --- caminho com espaco ---
echo %CD%| findstr /c:" " >nul
if not errorlevel 1 (
  echo.
  echo  AVISO: o caminho do projeto tem espaco no nome:
  echo    %CD%
  echo  Somado ao nome de cada musica e das faixas, isso pode estourar
  echo  o limite de 260 caracteres do Windows e fazer arquivos sumirem
  echo  sem aviso nenhum. O ideal e mover tudo para C:\KaraokeSep.
  echo  AVISO: caminho com espaco: %CD% >> "%LOG%"
  echo.
  choice /c SN /t 20 /d S /m "Continuar assim mesmo"
  if errorlevel 2 (
    echo  Cancelado. Mova a pasta e rode de novo.
    pause
    exit /b 1
  )
)

rem --- caminho de rede ---
echo %CD%| findstr /b "\\\\" >nul
if not errorlevel 1 (
  echo.
  echo  ERRO: o projeto esta numa pasta de rede ^(%CD%^).
  echo  O ambiente virtual do Python nao funciona em caminho UNC.
  echo  Copie para um disco local, por exemplo C:\KaraokeSep.
  echo  ERRO: caminho UNC >> "%LOG%"
  echo.
  pause
  exit /b 1
)

rem --- OneDrive ---
echo %CD%| findstr /i "OneDrive" >nul
if not errorlevel 1 (
  echo.
  echo  AVISO: a pasta esta dentro do OneDrive.
  echo  A sincronizacao trava arquivos em uso e pode corromper o venv,
  echo  alem de subir gigabytes de modelos para a nuvem sem necessidade.
  echo  Recomendo fortemente mover para C:\KaraokeSep.
  echo  AVISO: dentro do OneDrive >> "%LOG%"
  echo.
  choice /c SN /t 20 /d N /m "Continuar assim mesmo"
  if errorlevel 2 (
    echo  Cancelado.
    pause
    exit /b 1
  )
)

rem --- permissao de escrita ---
echo teste > "%~dp0_escrita.tmp" 2>nul
if not exist "%~dp0_escrita.tmp" (
  echo.
  echo  ERRO: sem permissao para gravar nesta pasta.
  echo  Mova o projeto para uma pasta sua, como C:\KaraokeSep,
  echo  ou rode este arquivo como administrador.
  echo  ERRO: sem permissao de escrita >> "%LOG%"
  echo.
  pause
  exit /b 1
)
del "%~dp0_escrita.tmp" >nul 2>&1

rem --- espaco em disco ---
for /f "tokens=3" %%A in ('dir /-c "%~dp0" ^| findstr /c:"bytes livres" /c:"bytes free"') do set "LIVRE=%%A"
if defined LIVRE (
  set /a "GB=!LIVRE:~0,-9!" 2>nul
  if defined GB (
    if !GB! LSS 12 (
      echo.
      echo  AVISO: restam cerca de !GB! GB neste disco.
      echo  A instalacao usa ~6 GB e os modelos somam mais alguns GB.
      echo  AVISO: apenas !GB! GB livres >> "%LOG%"
      echo.
      choice /c SN /t 20 /d S /m "Continuar"
      if errorlevel 2 exit /b 1
    )
  )
)

rem --- requirements.txt presente ---
if not exist "requirements.txt" (
  echo.
  echo  ERRO: requirements.txt nao encontrado nesta pasta.
  echo  O zip foi descompactado pela metade, ou o instalar.bat foi
  echo  copiado sozinho. Descompacte o projeto inteiro e tente de novo.
  echo  ERRO: requirements.txt ausente >> "%LOG%"
  echo.
  pause
  exit /b 1
)
if not exist "app\main.py" (
  echo.
  echo  ERRO: a pasta app nao foi encontrada.
  echo  Descompacte o zip inteiro, mantendo a estrutura de pastas.
  echo  ERRO: pasta app ausente >> "%LOG%"
  echo.
  pause
  exit /b 1
)

echo       ok
echo.

rem ==================================================================
rem  ETAPA 1 - Python compativel
rem ==================================================================
echo [1/7] Procurando Python 3.10, 3.11 ou 3.12...
echo. >> "%LOG%"
echo --- ETAPA 1: procura de Python --- >> "%LOG%"

set "PY="
set "VER="

rem --- 1a: py launcher ---
where py >nul 2>&1
if not errorlevel 1 (
  for %%V in (3.12 3.11 3.10) do (
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

rem --- 1b: python do PATH ---
if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 (
    set "RAW="
    for /f "tokens=2 delims= " %%A in ('python -V 2^>^&1') do set "RAW=%%A"
    if defined RAW (
      for /f "tokens=1,2 delims=." %%A in ("!RAW!") do (
        set "MA=%%A"
        set "MI=%%B"
      )
      echo python do PATH: !RAW! >> "%LOG%"
      if "!MA!"=="3" (
        if !MI! GEQ 10 if !MI! LEQ 12 (
          set "PY=python"
          set "VER=!MA!.!MI!"
        )
      )
    )
  )
)

rem --- 1c: caminhos padrao (util logo apos winget, com PATH desatualizado) ---
if not defined PY (
  for %%V in (312 311 310) do (
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

rem --- 1d: instalar pelo winget ---
if not defined PY (
  echo.
  echo  Nenhum Python compativel encontrado.
  echo.
  echo  Este projeto exige Python 3.10, 3.11 ou 3.12.
  echo  O 3.13 e o 3.14 NAO servem: a biblioteca diffq-fixed, exigida
  echo  pelo audio-separator, nao compila nessas versoes ^(erro
  echo  "bitpack.pyx doesn't match any files"^).
  echo.
  echo nenhum Python compativel encontrado >> "%LOG%"

  where winget >nul 2>&1
  if errorlevel 1 (
    echo  O winget nao esta disponivel nesta maquina.
    echo.
    echo  Baixe o Python 3.12 manualmente em:
    echo    https://www.python.org/downloads/release/python-3129/
    echo  Escolha "Windows installer ^(64-bit^)" e marque a caixa
    echo  "Add python.exe to PATH" na primeira tela do instalador.
    echo.
    echo  Depois rode este arquivo de novo.
    echo  ERRO: winget indisponivel >> "%LOG%"
    echo.
    pause
    exit /b 1
  )

  echo  Posso instalar o Python 3.12 agora pelo winget ^(~30 MB^).
  echo  Voce NAO precisa desinstalar o Python que ja tem.
  echo  Pode aparecer uma janela de permissao do Windows.
  echo.
  choice /c SN /m "Instalar agora"
  if errorlevel 2 (
    echo  Cancelado pelo usuario.
    echo cancelado pelo usuario na instalacao do Python >> "%LOG%"
    pause
    exit /b 1
  )

  echo.
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements >> "%LOG%" 2>&1
  set "RC=!errorlevel!"
  echo winget terminou com codigo !RC! >> "%LOG%"

  py -3.12 -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    set "PY=py -3.12"
    set "VER=3.12"
  ) else (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
      set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
      set "VER=3.12"
    )
  )
)

if not defined PY (
  echo.
  echo  O Python foi instalado, mas esta janela do cmd ainda nao
  echo  enxerga o novo PATH. Isso e normal no Windows.
  echo.
  echo  FECHE esta janela, abra outra e rode instalar.bat de novo.
  echo.
  echo  Se der a mesma mensagem duas vezes, instale manualmente por:
  echo    https://www.python.org/downloads/release/python-3129/
  echo  marcando "Add python.exe to PATH".
  echo  PATH nao atualizado apos instalacao >> "%LOG%"
  echo.
  pause
  exit /b 1
)

echo       Python %VER% encontrado
echo Python escolhido: %PY% ^(%VER%^) >> "%LOG%"
echo.

rem ==================================================================
rem  ETAPA 2 - ambiente virtual
rem ==================================================================
echo [2/7] Preparando o ambiente virtual...
echo. >> "%LOG%"
echo --- ETAPA 2: venv --- >> "%LOG%"

if exist "venv\Scripts\python.exe" (
  set "VVER="
  for /f "tokens=2 delims= " %%A in ('venv\Scripts\python.exe -V 2^>^&1') do set "VVER=%%A"
  echo venv existente: !VVER! >> "%LOG%"
  if not defined VVER (
    echo       ambiente existente esta quebrado; recriando...
    rmdir /s /q venv 2>nul
  ) else (
    echo !VVER! | findstr /b "3.13 3.14 3.15 3.9 3.8" >nul
    if not errorlevel 1 (
      echo       ambiente existente usa Python !VVER!, que nao serve. Recriando...
      echo recriando venv incompativel !VVER! >> "%LOG%"
      rmdir /s /q venv 2>nul
    )
  )
)

if exist "venv" if not exist "venv\Scripts\python.exe" (
  echo       restos de um venv incompleto; limpando...
  rmdir /s /q venv 2>nul
)

if not exist "venv\Scripts\python.exe" (
  echo       criando...
  %PY% -m venv venv >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo.
    echo  ERRO: nao consegui criar o ambiente virtual.
    echo.
    echo  Causas comuns:
    echo   - antivirus bloqueando a criacao da pasta venv
    echo   - pasta venv aberta no Explorer ou em uso por outro programa
    echo   - instalacao do Python incompleta ^(reinstale marcando "pip"^)
    echo.
    echo  Veja o detalhe no final de instalacao.log
    echo.
    pause
    exit /b 1
  )
)

set "VPY=venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo.
  echo  ERRO: o venv foi criado mas o python.exe nao esta la dentro.
  echo  Apague a pasta venv e rode de novo.
  echo ERRO: venv sem python.exe >> "%LOG%"
  echo.
  pause
  exit /b 1
)
echo       ok
echo.

rem ==================================================================
rem  ETAPA 3 - pip
rem ==================================================================
echo [3/7] Atualizando o pip...
echo. >> "%LOG%"
echo --- ETAPA 3: pip --- >> "%LOG%"
"%VPY%" -m pip install --upgrade pip setuptools wheel >> "%LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo  ERRO: falha ao atualizar o pip.
  echo.
  echo  Quase sempre e rede: proxy da empresa, firewall ou antivirus
  echo  bloqueando o acesso a pypi.org.
  echo.
  echo  Teste abrindo https://pypi.org no navegador.
  echo  Se estiver atras de proxy, configure a variavel HTTPS_PROXY
  echo  antes de rodar este arquivo.
  echo.
  pause
  exit /b 1
)
echo       ok
echo.

rem ==================================================================
rem  ETAPA 4 - acelerador
rem ==================================================================
echo [4/7] Detectando acelerador...
set "ACEL=cpu"
where nvidia-smi >nul 2>&1
if not errorlevel 1 (
  nvidia-smi >nul 2>&1
  if not errorlevel 1 (
    set "ACEL=gpu"
    for /f "tokens=*" %%A in ('nvidia-smi --query-gpu^=name^,memory.total --format^=csv^,noheader 2^>nul') do (
      echo       GPU: %%A
      echo GPU detectada: %%A >> "%LOG%"
    )
  )
)
if "%ACEL%"=="cpu" (
  echo       Nenhuma GPU NVIDIA. Modo CPU.
  echo       A separacao vai levar minutos por musica, nao segundos.
  echo modo CPU >> "%LOG%"
)
echo.

rem ==================================================================
rem  ETAPA 5 - audio-separator
rem ==================================================================
echo [5/7] Instalando o audio-separator...
echo       Sao mais de 2 GB. Pode demorar bastante.
echo       Nao feche esta janela.
echo. >> "%LOG%"
echo --- ETAPA 5: audio-separator ^(%ACEL%^) --- >> "%LOG%"
echo.

if "%ACEL%"=="gpu" (
  "%VPY%" -m pip install "audio-separator[gpu]"
) else (
  "%VPY%" -m pip install "audio-separator[cpu]"
)
if errorlevel 1 (
  echo.
  echo  ============================================
  echo   ERRO ao instalar o audio-separator
  echo  ============================================
  echo.
  echo  Procure no texto acima qual destes aparece:
  echo.
  echo  1^) "bitpack.pyx doesn't match any files"
  echo     ou qualquer mencao a diffq-fixed
  echo     --^> o venv esta com Python incompativel.
  echo         Apague a pasta venv e rode este arquivo de novo.
  echo.
  echo  2^) "Microsoft Visual C++ 14.0 or greater is required"
  echo     --^> falta compilador. Instale o Build Tools marcando
  echo         "Desenvolvimento para desktop com C++":
  echo         https://visualstudio.microsoft.com/visual-cpp-build-tools/
  echo         Sao uns 6 GB. Se aparecer isso, me avise antes:
  echo         talvez de para resolver de outro jeito.
  echo.
  echo  3^) "No space left" ou "There is not enough space"
  echo     --^> libere espaco no disco e rode de novo.
  echo.
  echo  4^) "Connection", "timed out", "Read timed out", "SSL"
  echo     --^> queda de rede. Rode de novo: o pip aproveita
  echo         o que ja foi baixado.
  echo.
  echo  5^) "Access is denied" ou "Permission denied"
  echo     --^> antivirus ou OneDrive travando arquivo.
  echo         Pause a sincronizacao e tente de novo.
  echo.
  echo  O texto completo esta em instalacao.log
  echo.
  echo ERRO na etapa 5 >> "%LOG%"
  pause
  exit /b 1
)
echo.
echo       ok
echo.

rem ==================================================================
rem  ETAPA 6 - demais dependencias
rem ==================================================================
echo [6/7] Instalando as demais dependencias...
echo. >> "%LOG%"
echo --- ETAPA 6: requirements.txt --- >> "%LOG%"
"%VPY%" -m pip install -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo  ERRO ao instalar as dependencias do requirements.txt.
  echo  Veja o final de instalacao.log.
  echo.
  echo  Se a falha for no gradio, tente manualmente:
  echo    venv\Scripts\python.exe -m pip install "gradio^>=4.44,^<6"
  echo.
  pause
  exit /b 1
)
echo       ok
echo.

rem ==================================================================
rem  ETAPA 7 - verificacao
rem ==================================================================
echo [7/7] Verificando...
echo. >> "%LOG%"
echo --- ETAPA 7: verificacao --- >> "%LOG%"

if not exist "modelos" mkdir modelos
if not exist "saida"   mkdir saida
if not exist "trabalho" mkdir trabalho
if not exist "bin"     mkdir bin

"%VPY%" -c "import sys, shutil, app.util, gradio, numpy, soundfile, requests, mutagen, psutil, imageio_ffmpeg, audioread; from PIL import Image; from audio_separator.separator import Separator; s=Separator(model_file_dir='modelos', log_level=40); n=sum(len(v) for v in s.list_supported_model_files().values()); print('      Python        ', sys.version.split()[0]); print('      gradio        ', gradio.__version__); print('      numpy         ', numpy.__version__); print('      ffmpeg        ', shutil.which('ffmpeg') or 'NAO ENCONTRADO'); print('      catalogo      ', n, 'familias de modelo')"
if errorlevel 1 (
  echo.
  echo  ============================================
  echo   A instalacao terminou mas algo nao carrega
  echo  ============================================
  echo.
  echo  Procure no erro acima:
  echo.
  echo  - "No module named 'audioread'"
  echo      venv\Scripts\python.exe -m pip install audioread
  echo.
  echo  - "FFmpeg is not installed"
  echo      apague a pasta bin e rode este arquivo de novo.
  echo      Se insistir:  winget install -e --id Gyan.FFmpeg
  echo      e abra um cmd novo depois.
  echo.
  echo  - "No module named 'app'"
  echo      voce rodou o .bat de outra pasta. Ele precisa estar
  echo      na mesma pasta onde fica a pasta app.
  echo.
  echo  - "DLL load failed"
  echo      instale o Visual C++ Redistributable 2015-2022 x64:
  echo      https://aka.ms/vs/17/release/vc_redist.x64.exe
  echo.
  echo  Copie o erro inteiro antes de fechar esta janela.
  echo  Ele tambem esta em instalacao.log
  echo.
  echo ERRO na verificacao final >> "%LOG%"
  pause
  exit /b 1
)

echo. >> "%LOG%"
echo INSTALACAO CONCLUIDA COM SUCESSO %DATE% %TIME% >> "%LOG%"

echo.
echo ============================================
echo  Instalacao concluida com Python %VER% ^(%ACEL%^).
echo ============================================
echo.
echo  1^) verificar_modelos.bat   lista os modelos disponiveis
echo  2^) rodar.bat               abre o programa
echo.
echo  Na primeira separacao ainda ha download dos pesos do
echo  modelo, algumas centenas de MB. Acontece uma vez so.
echo.
echo  Para levar a outro PC: copie a pasta SEM o venv e rode
echo  instalar.bat la. A pasta modelos pode ir junto e evita
echo  baixar os pesos de novo.
echo.
pause
