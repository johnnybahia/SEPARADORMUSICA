"""Atualizacao do programa pelo GitHub, por um botao no modo festa.

Como funciona
  1. consulta o ultimo commit do ramo (padrao: main) e compara com versao.json,
     que guarda o commit instalado;
  2. baixa o zip desse commit, extrai numa pasta temporaria e CONFERE antes de
     tocar em qualquer coisa (caminhos seguros, arquivos essenciais, todo .py
     compila);
  3. guarda uma copia do que vai trocar em trabalho/backup e troca arquivo por
     arquivo; qualquer falha restaura a copia;
  4. se o requirements.txt mudou, roda o pip no ambiente atual;
  5. reinicia o servidor.

O que NAO e tocado: venv, saida, modelos, trabalho, bin, logs, tempos.json e
os arquivos de configuracao. Os .bat da raiz tambem ficam como estao: um .bat
em execucao nao pode ser sobrescrito com seguranca. Se algum mudou no GitHub,
o aviso final diz quais, para trocar a mao.

Repositorio privado: ponha um token do GitHub (so leitura) em github_token.txt
ao lado do festa.bat. Outro repositorio ou ramo: atualizacao.json com
{"repo": "dono/nome", "ramo": "main"}.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shutil
import stat
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable

import requests

from .util import RAIZ, log

REPO_PADRAO = "johnnybahia/separadormusica"
RAMO_PADRAO = "main"
API_PADRAO = "https://api.github.com"

LIMITE_ZIP = 150 * 1024 * 1024            # baixado
LIMITE_EXTRAIDO = 400 * 1024 * 1024       # descompactado (guarda contra zip bomba)
LIMITE_ARQUIVOS = 5000
VALIDADE_CACHE = 600                      # s: nao consulta o GitHub a cada abertura
MANTER_BACKUPS = 3

# Nomes da raiz que nunca sao tocados.
PROTEGIDOS = {"venv", "saida", "modelos", "trabalho", "bin", "__pycache__",
              "karaokesep.log", "tempos.json", "versao.json",
              "atualizacao.json", "github_token.txt"}
ESSENCIAIS = ("app/__init__.py", "app/festa.py", "app/atualizar.py",
              "app/util.py", "requirements.txt")

FASES = {"consultando": "consultando o GitHub", "baixando": "baixando",
         "extraindo": "extraindo", "validando": "conferindo os arquivos",
         "aplicando": "trocando os arquivos", "dependencias": "instalando bibliotecas",
         "reiniciando": "reiniciando", "pronto": "ja estava atualizado"}


class ErroAtualizacao(Exception):
    """Falha com mensagem para mostrar a pessoa."""


# --------------------------------------------------------------------------- #
#  Configuracao e versao instalada
# --------------------------------------------------------------------------- #
def _config(raiz: Path) -> dict:
    cfg = {"repo": REPO_PADRAO, "ramo": RAMO_PADRAO, "api": API_PADRAO, "token": ""}
    try:
        cfg.update({k: v for k, v in
                    json.loads((raiz / "atualizacao.json").read_text("utf-8")).items()
                    if k in cfg and isinstance(v, str) and v})
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("atualizacao.json ilegivel: %s", e)
    if not cfg["token"]:
        try:
            cfg["token"] = (raiz / "github_token.txt").read_text("utf-8").strip()
        except OSError:
            cfg["token"] = os.environ.get("GITHUB_TOKEN", "").strip()
    return cfg


def ler_versao(raiz: Path = RAIZ) -> dict:
    try:
        d = json.loads((raiz / "versao.json").read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _cabecalhos(cfg: dict) -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "KaraokeSep-atualizador"}
    if cfg["token"]:
        h["Authorization"] = f"Bearer {cfg['token']}"
    return h


# --------------------------------------------------------------------------- #
#  Consulta
# --------------------------------------------------------------------------- #
_cache = {"quando": 0.0, "chave": None, "dados": None}


def consultar_remoto(raiz: Path = RAIZ, forcar: bool = False) -> dict:
    """{"sha", "mensagem", "data"} do ultimo commit. Levanta ErroAtualizacao."""
    cfg = _config(raiz)
    chave = (cfg["api"], cfg["repo"], cfg["ramo"])
    if (not forcar and _cache["chave"] == chave and _cache["dados"]
            and time.time() - _cache["quando"] < VALIDADE_CACHE):
        return _cache["dados"]
    url = f"{cfg['api']}/repos/{cfg['repo']}/commits/{cfg['ramo']}"
    try:
        r = requests.get(url, headers=_cabecalhos(cfg), timeout=8)
    except requests.RequestException as e:
        raise ErroAtualizacao("sem conexao com o GitHub") from e
    if r.status_code in (401, 403, 404):
        if r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
            raise ErroAtualizacao("limite de consultas do GitHub atingido; "
                                  "tente de novo daqui a uma hora")
        raise ErroAtualizacao("repositorio nao encontrado ou privado "
                              "(veja github_token.txt no LEIAME)")
    if r.status_code != 200:
        raise ErroAtualizacao(f"GitHub respondeu {r.status_code}")
    try:
        j = r.json()
        dados = {"sha": j["sha"],
                 "mensagem": (j.get("commit", {}).get("message") or "").split("\n")[0][:140],
                 "data": j.get("commit", {}).get("committer", {}).get("date", "")}
    except Exception as e:
        raise ErroAtualizacao("resposta inesperada do GitHub") from e
    _cache.update(quando=time.time(), chave=chave, dados=dados)
    return dados


def verificar(raiz: Path = RAIZ, forcar: bool = False) -> dict:
    """O que a pagina mostra: versao instalada, ultima do GitHub, se ha nova."""
    local = ler_versao(raiz)
    saida = {"local": local.get("sha", ""), "local_data": local.get("data", ""),
             "remoto": "", "mensagem": "", "novo": False, "erro": ""}
    try:
        r = consultar_remoto(raiz, forcar)
    except ErroAtualizacao as e:
        saida["erro"] = str(e)
        return saida
    saida.update(remoto=r["sha"], mensagem=r["mensagem"],
                 novo=r["sha"] != saida["local"])
    return saida


# --------------------------------------------------------------------------- #
#  Download e extracao
# --------------------------------------------------------------------------- #
def _baixar(raiz: Path, sha: str, pasta: Path, progresso: Callable[[float], None]) -> Path:
    cfg = _config(raiz)
    url = f"{cfg['api']}/repos/{cfg['repo']}/zipball/{sha}"
    destino = pasta / f"{sha}.zip"
    try:
        with requests.get(url, headers=_cabecalhos(cfg), stream=True, timeout=30) as r:
            if r.status_code != 200:
                raise ErroAtualizacao(f"nao consegui baixar (GitHub {r.status_code})")
            total = int(r.headers.get("Content-Length") or 0)
            feito = 0
            with open(destino, "wb") as f:
                for bloco in r.iter_content(256 * 1024):
                    feito += len(bloco)
                    if feito > LIMITE_ZIP:
                        raise ErroAtualizacao("o download passou do limite de tamanho")
                    f.write(bloco)
                    if total:
                        progresso(feito / total)
    except requests.RequestException as e:
        raise ErroAtualizacao("a conexao caiu durante o download") from e
    if not zipfile.is_zipfile(destino):
        raise ErroAtualizacao("o arquivo baixado nao e um zip valido")
    return destino


def _extrair(zip_path: Path, destino: Path) -> list[Path]:
    """Extrai sem a pasta-raiz que o GitHub poe no zip. Recusa caminho fora do
    destino, link simbolico e zip grande demais. Devolve os arquivos (relativos)."""
    destino.mkdir(parents=True, exist_ok=True)
    base = destino.resolve()
    arquivos: list[Path] = []
    total = 0
    with zipfile.ZipFile(zip_path) as z:
        itens = z.infolist()
        if len(itens) > LIMITE_ARQUIVOS:
            raise ErroAtualizacao("zip com arquivos demais")
        for info in itens:
            nome = info.filename.replace("\\", "/")
            partes = [p for p in nome.split("/")]
            if len(partes) < 2 or not partes[0]:
                continue                                   # a pasta-raiz em si
            rel = posixpath.normpath("/".join(partes[1:]))
            if info.is_dir() or rel in ("", "."):
                continue
            if rel.startswith("/") or rel.split("/")[0] == ".." or ":" in rel.split("/")[0]:
                raise ErroAtualizacao(f"caminho suspeito no zip: {nome}")
            if stat.S_ISLNK(info.external_attr >> 16):
                continue                                   # link simbolico: ignora
            total += info.file_size
            if total > LIMITE_EXTRAIDO:
                raise ErroAtualizacao("o zip descompactado passou do limite")
            alvo = (base / rel).resolve()
            if base not in alvo.parents:
                raise ErroAtualizacao(f"caminho suspeito no zip: {nome}")
            alvo.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as origem, open(alvo, "wb") as f:
                shutil.copyfileobj(origem, f)
            arquivos.append(Path(rel))
    return arquivos


def _validar(novo: Path, arquivos: list[Path]) -> None:
    presentes = {a.as_posix() for a in arquivos}
    faltam = [e for e in ESSENCIAIS if e not in presentes]
    if faltam:
        raise ErroAtualizacao("a versao baixada esta incompleta: falta "
                              + ", ".join(faltam))
    for a in arquivos:
        if a.suffix == ".py":
            try:
                compile((novo / a).read_text("utf-8"), a.as_posix(), "exec")
            except SyntaxError as e:
                raise ErroAtualizacao(f"{a.as_posix()} nao compila "
                                      f"(linha {e.lineno}); atualizacao cancelada") from e


# --------------------------------------------------------------------------- #
#  Troca de arquivos, com restauracao
# --------------------------------------------------------------------------- #
def _vai_para_destino(rel: Path) -> bool:
    p = rel.parts
    if p[0] in PROTEGIDOS or p[0].startswith("."):
        return False
    if len(p) == 1 and rel.suffix.lower() == ".bat":
        return False
    return True


def _aplicar(raiz: Path, novo: Path, arquivos: list[Path], backup: Path
             ) -> tuple[int, list[str]]:
    """Copia os arquivos novos por cima. Devolve (quantos, .bat que mudaram).
    Se algo falhar, desfaz tudo e relevanta o erro."""
    backup.mkdir(parents=True, exist_ok=True)
    destinos = [a for a in arquivos if _vai_para_destino(a)]
    bats = sorted(a.name for a in arquivos
                  if len(a.parts) == 1 and a.suffix.lower() == ".bat"
                  and (not (raiz / a).exists()
                       or (raiz / a).read_bytes() != (novo / a).read_bytes()))

    # arquivos de app/ que sairam da versao nova: removidos (ficam no backup)
    novos = {a.as_posix() for a in destinos}
    sobrando = []
    if (raiz / "app").is_dir():
        for f in (raiz / "app").rglob("*"):
            rel = f.relative_to(raiz)
            if f.is_file() and "__pycache__" not in rel.parts \
                    and rel.as_posix() not in novos:
                sobrando.append(rel)

    guardados, criados = [], []
    try:
        for rel in [*destinos, *sobrando]:
            atual = raiz / rel
            if atual.exists():
                (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(atual, backup / rel)
                guardados.append(rel)
        for rel in destinos:
            alvo = raiz / rel
            alvo.parent.mkdir(parents=True, exist_ok=True)
            if not alvo.exists():
                criados.append(rel)
            tmp = alvo.with_name(alvo.name + ".novo")
            shutil.copyfile(novo / rel, tmp)
            os.replace(tmp, alvo)
        for rel in sobrando:
            (raiz / rel).unlink()
    except Exception:
        _restaurar(raiz, backup, guardados, criados)
        raise
    return len(destinos), bats


def _restaurar(raiz: Path, backup: Path, guardados: list[Path], criados: list[Path]) -> None:
    for rel in criados:
        try:
            (raiz / rel).unlink()
        except OSError:
            pass
    for rel in guardados:
        try:
            (raiz / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / rel, raiz / rel)
        except OSError as e:
            log.error("Nao consegui restaurar %s: %s", rel, e)
    for tmp in raiz.rglob("*.novo"):
        try:
            tmp.unlink()
        except OSError:
            pass


def _podar_backups(pasta: Path) -> None:
    if not pasta.is_dir():
        return
    antigos = sorted((d for d in pasta.iterdir() if d.is_dir()),
                     key=lambda d: d.stat().st_mtime, reverse=True)[MANTER_BACKUPS:]
    for d in antigos:
        shutil.rmtree(d, ignore_errors=True)


def _hash(arq: Path) -> str:
    try:
        return hashlib.sha256(arq.read_bytes()).hexdigest()
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
#  Reinicio
# --------------------------------------------------------------------------- #
CODIGO_REINICIO = ("import time; time.sleep(3); import app.festa as f; f.gerar(); "
                   "f.servidor_ligado(); [time.sleep(3600) for _ in iter(int, 1)]")


def reiniciar(raiz: Path = RAIZ) -> None:
    """Sobe um servidor novo e encerra este. O novo espera 3 s para o antigo
    soltar a porta. No Windows ganha uma janela propria (a do festa.bat vai
    pedir uma tecla e pode ser fechada)."""
    kw: dict = {"cwd": str(raiz), "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    else:
        kw["start_new_session"] = True
    subprocess.Popen([sys.executable, "-c", CODIGO_REINICIO], **kw)
    threading.Timer(1.5, lambda: os._exit(0)).start()


REINICIAR: Callable[[Path], None] = reiniciar        # trocavel nos testes


# --------------------------------------------------------------------------- #
#  Tarefa em segundo plano
# --------------------------------------------------------------------------- #
_trava = threading.Lock()
_estado: dict = {"fase": "", "texto": "", "frac": 0.0, "erro": "", "aviso": "",
                 "rodando": False}


def estado() -> dict:
    with _trava:
        return dict(_estado)


def _fase(fase: str, frac: float = 0.0, texto: str = "") -> None:
    with _trava:
        _estado.update(fase=fase, frac=frac, texto=texto or FASES.get(fase, fase))
    log.info("Atualizacao: %s", texto or FASES.get(fase, fase))


def iniciar(raiz: Path = RAIZ, ocupado: Callable[[], bool] = lambda: False) -> dict:
    with _trava:
        if _estado["rodando"]:
            return {"erro": "ja ha uma atualizacao em andamento"}
        if ocupado():
            return {"erro": "ha separacao em andamento ou na fila; espere terminar "
                            "(a atualizacao reinicia o servidor)"}
        _estado.update(fase="consultando", texto=FASES["consultando"], frac=0.0,
                       erro="", aviso="", rodando=True)
    threading.Thread(target=_executar, args=(raiz,), daemon=True).start()
    return {"ok": True}


def _executar(raiz: Path) -> None:
    try:
        _fase("consultando")
        remoto = consultar_remoto(raiz, forcar=True)
        local = ler_versao(raiz).get("sha", "")
        if local == remoto["sha"]:
            _fase("pronto")
            return

        trabalho = raiz / "trabalho" / "atualizacao"
        shutil.rmtree(trabalho, ignore_errors=True)
        trabalho.mkdir(parents=True)

        _fase("baixando")
        zip_path = _baixar(raiz, remoto["sha"], trabalho,
                           lambda f: _fase("baixando", f))
        _fase("extraindo")
        novo = trabalho / "novo"
        arquivos = _extrair(zip_path, novo)
        _fase("validando")
        _validar(novo, arquivos)

        _fase("aplicando")
        antes_req = _hash(raiz / "requirements.txt")
        rotulo = (local or "sem-versao")[:7] + time.strftime("-%Y%m%d-%H%M%S")
        backup = raiz / "trabalho" / "backup" / rotulo
        n, bats = _aplicar(raiz, novo, arquivos, backup)

        if _hash(raiz / "requirements.txt") != antes_req:
            _fase("dependencias", texto="instalando bibliotecas novas (pode demorar)")
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                                str(raiz / "requirements.txt")],
                               capture_output=True, text=True, timeout=1800)
            if r.returncode != 0:
                # codigo novo sem as bibliotecas dele quebra; volta ao antigo
                _desfazer_tudo(raiz, backup)
                cauda = (r.stderr or r.stdout).strip().splitlines()[-3:]
                raise ErroAtualizacao("o pip falhou; o programa voltou a versao "
                                      "anterior. " + " | ".join(cauda))

        (raiz / "versao.json").write_text(json.dumps(
            {"sha": remoto["sha"], "mensagem": remoto["mensagem"],
             "data": datetime.now().isoformat(timespec="seconds")},
            ensure_ascii=False), encoding="utf-8")
        _podar_backups(raiz / "trabalho" / "backup")
        shutil.rmtree(trabalho, ignore_errors=True)
        _cache["dados"] = None

        aviso = f"{n} arquivos atualizados."
        if bats:
            aviso += (" Estes .bat mudaram no GitHub e nao foram trocados "
                      "(baixe-os a mao): " + ", ".join(bats))
        with _trava:
            _estado["aviso"] = aviso
        _fase("reiniciando")
        REINICIAR(raiz)
    except Exception as e:
        msg = str(e) if isinstance(e, ErroAtualizacao) else f"{type(e).__name__}: {e}"
        log.error("Atualizacao falhou: %s", msg, exc_info=not isinstance(e, ErroAtualizacao))
        with _trava:
            _estado["erro"] = msg
    finally:
        with _trava:
            _estado["rodando"] = False


def _desfazer_tudo(raiz: Path, backup: Path) -> None:
    """Volta app/ e os arquivos da raiz ao que estava no backup."""
    for f in backup.rglob("*"):
        if f.is_file():
            rel = f.relative_to(backup)
            (raiz / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, raiz / rel)
