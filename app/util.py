"""Utilidades de baixo nivel: ffmpeg, caminhos, nomes de arquivo, log."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA_MODELOS = RAIZ / "modelos"
PASTA_SAIDA = RAIZ / "saida"
PASTA_TRABALHO = RAIZ / "trabalho"
PASTA_BIN = RAIZ / "bin"
for _p in (PASTA_MODELOS, PASTA_SAIDA, PASTA_TRABALHO):
    _p.mkdir(parents=True, exist_ok=True)

EXTENSOES_AUDIO = {
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus",
    ".wma", ".aiff", ".aif", ".mp4", ".mkv", ".webm", ".avi",
}

log = logging.getLogger("karaokesep")
if not log.handlers:
    log.setLevel(logging.INFO)
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    _fh = logging.FileHandler(RAIZ / "karaokesep.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s"))
    log.addHandler(_fh)


def _preparar_ffmpeg_no_path() -> None:
    """Garante que exista um 'ffmpeg' no PATH do processo.

    O audio-separator nao aceita um caminho de binario: ele roda
    subprocess(["ffmpeg", "-version"]) e recusa a inicializar se falhar.
    O ffmpeg que vem no pacote imageio-ffmpeg tem nome versionado e nao
    esta no PATH, entao criamos um 'ffmpeg.exe' em bin/ (link fisico
    quando o disco permite, copia quando nao) e prefixamos o PATH.
    Assim o projeto funciona em qualquer PC sem instalar ffmpeg no
    sistema.
    """
    if shutil.which("ffmpeg"):
        return
    try:
        import imageio_ffmpeg
        origem = Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception as e:
        log.warning("Sem ffmpeg no PATH e sem imageio-ffmpeg: %s", e)
        return
    if not origem.exists():
        return

    destino = PASTA_BIN / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    try:
        PASTA_BIN.mkdir(parents=True, exist_ok=True)
        precisa = (not destino.exists()
                   or destino.stat().st_size != origem.stat().st_size)
        if precisa:
            if destino.exists():
                destino.unlink()
            try:
                os.link(origem, destino)      # link fisico: nao duplica espaco
            except Exception:
                shutil.copy2(origem, destino)  # discos ou volumes diferentes
            if os.name != "nt":
                destino.chmod(0o755)
    except Exception as e:
        log.warning("Nao consegui preparar o ffmpeg em bin/: %s", e)
        return

    os.environ["PATH"] = str(PASTA_BIN) + os.pathsep + os.environ.get("PATH", "")


_preparar_ffmpeg_no_path()


def ffmpeg_exe() -> str:
    """Binario do ffmpeg. Usa o do PATH se existir, senao o embutido no pip."""
    do_path = shutil.which("ffmpeg")
    if do_path:
        return do_path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "ffmpeg nao encontrado. Rode instalar.bat novamente ou instale o ffmpeg."
        ) from e


def rodar_ffmpeg(args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    """Executa o ffmpeg sem abrir janela de console."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-y"] + args
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    log.debug("ffmpeg %s", " ".join(args))
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, creationflags=flags,
    )
    if proc.returncode != 0:
        cauda = (proc.stderr or "").strip().splitlines()[-12:]
        raise RuntimeError("ffmpeg falhou:\n" + "\n".join(cauda))
    return proc


def info_ffmpeg(caminho: Path) -> str:
    """Devolve o stderr do ffmpeg -i, que contem codec, bitrate, canais e taxa."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(caminho)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", creationflags=flags,
    )
    return proc.stderr or ""


_INVALIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def nome_seguro(texto: str, limite: int = 60) -> str:
    """Nome de arquivo valido no Windows, sem acento, curto por causa do MAX_PATH."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = _INVALIDOS.sub("_", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" .")
    if not texto:
        texto = "faixa"
    if len(texto) > limite:
        texto = texto[:limite].rstrip(" .")
    return texto


def nome_unico(base: Path) -> Path:
    """Acrescenta sufixo numerico ate o caminho nao existir."""
    if not base.exists():
        return base
    pai, haste, ext = base.parent, base.stem, base.suffix
    for i in range(2, 1000):
        cand = pai / f"{haste}_{i}{ext}"
        if not cand.exists():
            return cand
    return pai / f"{haste}_{os.getpid()}{ext}"


def hash_curto(caminho: Path) -> str:
    """Identificador estavel e curto para a pasta de trabalho."""
    chave = f"{caminho.resolve()}|{caminho.stat().st_size}"
    return hashlib.sha1(chave.encode("utf-8")).hexdigest()[:10]


def abrir_pasta(caminho: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(caminho))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(caminho)])
        else:
            subprocess.Popen(["xdg-open", str(caminho)])
    except Exception as e:
        log.warning("Nao consegui abrir a pasta: %s", e)


def mm_ss(segundos: float) -> str:
    segundos = max(0, int(round(segundos)))
    return f"{segundos // 60}:{segundos % 60:02d}"


def listar_audios(caminhos: list[str]) -> list[Path]:
    """Expande arquivos e pastas em uma lista de audios suportados."""
    achados: list[Path] = []
    for bruto in caminhos or []:
        p = Path(bruto)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in EXTENSOES_AUDIO:
                    achados.append(f)
        elif p.is_file() and p.suffix.lower() in EXTENSOES_AUDIO:
            achados.append(p)
    vistos, unicos = set(), []
    for f in achados:
        chave = str(f.resolve()).lower()
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(f)
    return unicos
