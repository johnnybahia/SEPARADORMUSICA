"""Busca de letra sincronizada no LRCLIB.

Sem chave e sem cadastro. O casamento exato exige duracao dentro de 2 segundos
do registro, entao ha um caminho alternativo por busca livre.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .util import log

BASE = "https://lrclib.net/api"
UA = "KaraokeSep/1.0 (uso pessoal; https://github.com/johnnybahia)"
TEMPO_LIMITE = 12


@dataclass
class Letra:
    titulo: str
    artista: str
    sincronizada: str | None
    simples: str | None
    fonte: str

    @property
    def tem_sincronia(self) -> bool:
        return bool(self.sincronizada and self.sincronizada.strip())


def _get(rota: str, params: dict) -> list | dict | None:
    try:
        r = requests.get(f"{BASE}/{rota}", params=params,
                         headers={"User-Agent": UA}, timeout=TEMPO_LIMITE)
        if r.status_code == 429:
            espera = int(r.headers.get("Retry-After", "5"))
            log.warning("LRCLIB pediu para esperar %ss", espera)
            time.sleep(min(espera, 30))
            return _get(rota, params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("LRCLIB indisponivel: %s", e)
        return None


import re as _re

# lixo que sites de download e canais grudam no nome do arquivo
_SUJEIRA = _re.compile(
    r"(?ix)"
    r"\s*(?:"
    r"[-_\s]*\b(?:tuberipper|ytmp3|y2mate|320ytmp3|mp3juices|savefrom|"
    r"onlymp3|flvto|clipconverter|ytb?mp3|snaptube|4kdownload|converto|"
    r"mp3quack|tubidy|vidmate)\b[a-z0-9._-]*"
    r"|\.(?:net|com|com\.br|org|io|cc|to|me|app|xyz)\b"
    r"|\[[^\]]*(?:official|video|audio|lyric|hd|4k|remaster|ao vivo|live)[^\]]*\]"
    r"|\([^)]*(?:official|video|audio|lyric|hd|4k|remaster)[^)]*\)"
    r"|\b(?:official\s+(?:music\s+)?video|official\s+audio|lyric\s+video|"
    r"videoclipe\s+oficial|clipe\s+oficial|audio\s+oficial)\b"
    r"|\b(?:hd|hq|4k|1080p|720p|full\s*hd)\b"
    r"|[-_\s]*\byoutube\b"
    r")"
)


def limpar_titulo(texto: str) -> str:
    """Tira nome de site, marcacao de clipe e sobras de numeracao."""
    # sublinhado conta como caractere de palavra, entao "Musica_y2mate" nao
    # tem fronteira antes do nome do site: troca antes de procurar
    t = _re.sub(r"[_]+", " ", texto or "")
    t = _SUJEIRA.sub(" ", t)
    t = _re.sub(r"^\s*\d{1,3}\s*[-._)]\s*", "", t)   # "01 - " no comeco
    t = _re.sub(r"[\(\[\{]\s*[\)\]\}]", " ", t)      # parenteses que ficaram vazios
    t = _re.sub(r"\s{2,}", " ", t)
    return t.strip(" -–—_.")


def ler_tags(caminho: Path) -> tuple[str, str, str]:
    """Titulo, artista e album a partir das tags; cai para o nome do arquivo."""
    titulo = artista = album = ""
    try:
        from mutagen import File as MFile
        m = MFile(str(caminho), easy=True)
        if m:
            titulo = (m.get("title") or [""])[0]
            artista = (m.get("artist") or [""])[0]
            album = (m.get("album") or [""])[0]
    except Exception as e:
        log.debug("Sem tags em %s: %s", caminho.name, e)
    if not titulo:
        haste = limpar_titulo(caminho.stem)
        if " - " in haste:
            a, t = haste.split(" - ", 1)
            artista = artista or a.strip()
            titulo = t.strip()
        else:
            titulo = haste.strip()
    return (limpar_titulo(titulo), limpar_titulo(artista), limpar_titulo(album))


def buscar(titulo: str, artista: str, album: str = "",
           duracao: float | None = None) -> Letra | None:
    if duracao:
        dados = _get("get", {
            "track_name": titulo, "artist_name": artista,
            "album_name": album or "", "duration": int(round(duracao)),
        })
        if isinstance(dados, dict):
            return Letra(dados.get("trackName", titulo), dados.get("artistName", artista),
                         dados.get("syncedLyrics"), dados.get("plainLyrics"),
                         "LRCLIB (casamento exato)")

    consulta = f"{artista} {titulo}".strip() or titulo
    dados = _get("search", {"q": consulta})
    if isinstance(dados, list) and dados:
        com_sincronia = [d for d in dados if d.get("syncedLyrics")]
        escolhido = None
        if duracao:
            candidatos = com_sincronia or dados
            escolhido = min(candidatos,
                            key=lambda d: abs((d.get("duration") or 0) - duracao))
            if abs((escolhido.get("duration") or 0) - duracao) > 15:
                escolhido = com_sincronia[0] if com_sincronia else dados[0]
        else:
            escolhido = com_sincronia[0] if com_sincronia else dados[0]
        return Letra(escolhido.get("trackName", titulo),
                     escolhido.get("artistName", artista),
                     escolhido.get("syncedLyrics"), escolhido.get("plainLyrics"),
                     "LRCLIB (busca)")
    return None


def salvar_lrc(letra: Letra, destino: Path) -> Path | None:
    if not letra.tem_sincronia:
        return None
    destino.parent.mkdir(parents=True, exist_ok=True)
    cabecalho = (f"[ti:{letra.titulo}]\n[ar:{letra.artista}]\n"
                 f"[re:KaraokeSep]\n")
    destino.write_text(cabecalho + letra.sincronizada, encoding="utf-8")
    return destino


def analisar_lrc(texto: str) -> list[dict]:
    """LRC -> lista de {t, texto}. Expande timestamps repetidos na mesma linha."""
    import re
    linhas = []
    padrao = re.compile(r"\[(\d+):(\d+)(?:[.:](\d{1,3}))?\]")
    for bruto in (texto or "").splitlines():
        marcas = list(padrao.finditer(bruto))
        if not marcas:
            continue
        conteudo = bruto[marcas[-1].end():].strip()
        for m in marcas:
            cent = m.group(3) or "0"
            cent = cent.ljust(3, "0")[:3]
            t = int(m.group(1)) * 60 + int(m.group(2)) + int(cent) / 1000
            linhas.append({"t": round(t, 3), "texto": conteudo})
    linhas.sort(key=lambda x: x["t"])
    return linhas
