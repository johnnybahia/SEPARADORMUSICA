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


class ErroRede(Exception):
    """Nao deu para falar com o LRCLIB (sem internet, bloqueio, servico fora)."""


def _get(rota: str, params: dict, tentativas: int = 2) -> list | dict | None:
    """None quando o LRCLIB responde "nao achei" (404, ou 400 por faltar campo).
    Levanta ErroRede quando nem chegou a responder: dizer "nada encontrado"
    nesse caso faz a pessoa trocar o nome da musica sem necessidade."""
    ultimo = None
    for i in range(tentativas):
        try:
            r = requests.get(f"{BASE}/{rota}", params=params,
                             headers={"User-Agent": UA}, timeout=TEMPO_LIMITE)
        except requests.RequestException as e:
            ultimo = type(e).__name__
            log.warning("LRCLIB sem resposta (%s): %s", rota, e)
            time.sleep(1.5)
            continue
        if r.status_code == 429:
            espera = int(r.headers.get("Retry-After", "5") or 5)
            log.warning("LRCLIB pediu para esperar %ss", espera)
            time.sleep(min(espera, 20))
            continue
        if r.status_code in (400, 404):
            return None
        if r.status_code >= 500:
            ultimo = f"o servico respondeu erro {r.status_code}"
            time.sleep(1.5)
            continue
        try:
            return r.json()
        except ValueError:
            ultimo = "resposta ilegivel"
    raise ErroRede(str(ultimo) if ultimo else "sem resposta")


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


def _norm(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", t or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return _re.sub(r"[^a-z0-9 ]+", " ", t).strip()


def _parecido(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _nucleo(titulo: str) -> str:
    """Titulo sem "(feat. ...)", "- ao vivo", "(remix)" e parecidos."""
    t = _re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", titulo or "")
    t = _re.split(r"\s+-\s+", t)[0]
    t = _re.sub(r"(?i)\b(feat|ft|part|participacao)\b.*$", " ", t)
    return _re.sub(r"\s{2,}", " ", t).strip()


def _consultas(titulo: str, artista: str, album: str, duracao: float | None):
    """(rotulo, rota, parametros), da mais precisa para a mais frouxa."""
    c = []
    if duracao and artista and album:
        c.append((f"exata: {artista} / {titulo}", "get", {
            "track_name": titulo, "artist_name": artista,
            "album_name": album, "duration": int(round(duracao))}))
    if artista:
        c.append((f"{artista} / {titulo}", "search",
                  {"track_name": titulo, "artist_name": artista}))
        c.append((f"{artista} {titulo}", "search", {"q": f"{artista} {titulo}"}))
    c.append((titulo, "search", {"q": titulo}))
    nucleo = _nucleo(titulo)
    if nucleo and _norm(nucleo) != _norm(titulo):
        c.append((nucleo, "search", {"q": f"{artista} {nucleo}".strip()}))
    # nome de pasta "Artista - Musica" que veio inteiro no campo da musica
    if not artista and " - " in titulo:
        a, t = titulo.split(" - ", 1)
        c.append((f"{a.strip()} / {t.strip()}", "search",
                  {"track_name": t.strip(), "artist_name": a.strip()}))
    # campos trocados: "Musica - Artista"
    if artista:
        c.append((f"trocado: {titulo} / {artista}", "search",
                  {"track_name": artista, "artist_name": titulo}))
    return c


def _escolher(cands: list[dict], titulo: str, artista: str,
              duracao: float | None) -> dict | None:
    """O melhor candidato: com sincronia, nome parecido e duracao proxima."""
    melhor, nota = None, -1.0
    for d in cands:
        nome = d.get("trackName") or d.get("name") or ""
        cantor = d.get("artistName") or ""
        # a pessoa (ou o nome da pasta) pode ter trocado artista e musica
        sim_t = max(_parecido(nome, titulo), _parecido(nome, _nucleo(titulo)),
                    _parecido(nome, artista))
        sim_a = (max(_parecido(cantor, artista), _parecido(cantor, titulo))
                 if artista else 0.5)
        pontos = sim_t * 2.0 + sim_a
        if duracao and d.get("duration"):
            dif = abs(d["duration"] - duracao)
            pontos += 1.0 if dif <= 3 else (0.5 if dif <= 15 else -0.5)
        if d.get("syncedLyrics"):
            pontos += 1.5
        if d.get("instrumental"):
            continue                     # instrumental nao tem letra
        # nome que nao tem nada a ver nao serve, mesmo com sincronia
        if sim_t < 0.45 and (not artista or sim_a < 0.45):
            continue
        if pontos > nota:
            melhor, nota = d, pontos
    return melhor


def buscar_detalhado(titulo: str, artista: str, album: str = "",
                     duracao: float | None = None
                     ) -> tuple[Letra | None, list[str], str]:
    """(letra, consultas feitas, erro de rede). Para a pagina poder dizer o
    que tentou, em vez de so "nada encontrado"."""
    feitas: list[str] = []
    cands: dict = {}
    falhas_rede = 0
    erro_rede = ""
    for rotulo, rota, params in _consultas(titulo, artista, album, duracao):
        try:
            dados = _get(rota, params)
        except ErroRede as e:
            falhas_rede += 1
            erro_rede = str(e)
            feitas.append(f"{rotulo} (sem resposta)")
            if falhas_rede >= 2:
                break                    # sem rede: insistir so demora
            continue
        achados = ([dados] if isinstance(dados, dict)
                   else (dados if isinstance(dados, list) else []))
        feitas.append(f"{rotulo} ({len(achados)})")
        for d in achados:
            if isinstance(d, dict):
                cands.setdefault(d.get("id") or id(d), d)
        escolhido = _escolher(list(cands.values()), titulo, artista, duracao)
        if escolhido and escolhido.get("syncedLyrics"):
            break                        # ja serve; nao precisa das frouxas
    escolhido = _escolher(list(cands.values()), titulo, artista, duracao)
    if not escolhido:
        return None, feitas, (erro_rede if not cands else "")
    return (Letra(escolhido.get("trackName") or titulo,
                  escolhido.get("artistName") or artista,
                  escolhido.get("syncedLyrics"), escolhido.get("plainLyrics"),
                  "LRCLIB"), feitas, "")


def buscar(titulo: str, artista: str, album: str = "",
           duracao: float | None = None) -> Letra | None:
    try:
        return buscar_detalhado(titulo, artista, album, duracao)[0]
    except Exception as e:
        log.warning("Busca de letra falhou: %s", e)
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
