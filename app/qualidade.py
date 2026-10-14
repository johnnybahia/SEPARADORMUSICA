"""Diagnostico do arquivo de entrada.

Mede o corte real de banda pela FFT, o que denuncia MP3 de 128 kbps
reetiquetado como 320. Mede tambem estereo falso e clipping.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from .util import info_ffmpeg, log

_RE_LINHA_AUDIO = re.compile(r"^\s*Stream #.*?Audio:.*$", re.IGNORECASE | re.MULTILINE)
_RE_CODEC = re.compile(r"Audio:\s*([\w.]+)", re.IGNORECASE)
_RE_TAXA = re.compile(r"(\d+)\s*Hz", re.IGNORECASE)
_RE_LAYOUT = re.compile(r"Hz,\s*([\w.()]+)", re.IGNORECASE)
_RE_KBPS = re.compile(r"(\d+)\s*kb/s", re.IGNORECASE)


@dataclass
class Diagnostico:
    arquivo: Path
    codec: str = "?"
    bitrate_kbps: int = 0
    taxa: int = 0
    canais: int = 0
    duracao: float = 0.0
    corte_khz: float = 0.0
    correlacao: float = 0.0
    clipping_pct: float = 0.0
    classe: str = "?"          # BOM, MEDIO, RUIM
    notas: list[str] = field(default_factory=list)

    def linha(self) -> list:
        return [
            self.arquivo.name,
            self.classe,
            f"{self.corte_khz:.1f} kHz",
            f"{self.bitrate_kbps} kbps" if self.bitrate_kbps else "-",
            f"{self.taxa} Hz",
            {1: "mono", 2: "estereo", 6: "5.1"}.get(self.canais, f"{self.canais} canais"),
            "; ".join(self.notas) or "-",
        ]


COLUNAS = ["Arquivo", "Classe", "Corte real", "Bitrate", "Taxa", "Canais", "Observacoes"]


_LAYOUTS = {"mono": 1, "stereo": 2, "5.1": 6, "5.1(side)": 6,
            "7.1": 8, "7.1(wide)": 8, "quad": 4, "downmix": 2}


def _ler_cabecalho(caminho: Path, d: Diagnostico) -> None:
    texto = info_ffmpeg(caminho)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", texto)
    if m:
        d.duracao = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

    linha = _RE_LINHA_AUDIO.search(texto)
    if linha:
        s = linha.group(0)
        if (c := _RE_CODEC.search(s)):
            d.codec = c.group(1)
        if (t := _RE_TAXA.search(s)):
            d.taxa = int(t.group(1))
        if (lay := _RE_LAYOUT.search(s)):
            d.canais = _LAYOUTS.get(lay.group(1).strip().lower(), 0)
        if (b := _RE_KBPS.search(s)):
            d.bitrate_kbps = int(b.group(1))

    if not d.bitrate_kbps:
        g = re.search(r"Duration:.*?bitrate:\s*(\d+)\s*kb/s", texto, re.IGNORECASE)
        if g:
            d.bitrate_kbps = int(g.group(1))


def _corte_de_banda(x: np.ndarray, taxa: int) -> float:
    """Frequencia onde o espectro cai de penhasco, em kHz.

    Nao adianta usar um limiar fixo abaixo do pico: musica tem inclinacao
    natural de grave para agudo e qualquer limiar absoluto dispara cedo.
    O que denuncia o codec e o degrau: a banda alta some de uma vez.
    Entao comparamos uma faixa de referencia com o topo do espectro e, se a
    queda for grande, procuramos a posicao do degrau.
    """
    n = 8192
    if x.ndim > 1:
        x = x.mean(axis=1)
    if len(x) < n * 4:
        return 0.0

    inicio, fim = int(len(x) * 0.15), int(len(x) * 0.85) - n
    if fim <= inicio:
        inicio, fim = 0, max(1, len(x) - n)
    passos = min(60, max(1, (fim - inicio) // n))
    pontos = np.linspace(inicio, fim, num=passos, dtype=int)

    janela = np.hanning(n)
    espectros = []
    for p in pontos:
        bloco = x[p:p + n]
        if len(bloco) < n:
            continue
        mag = np.abs(np.fft.rfft(bloco * janela))
        if mag.max() > 0:
            espectros.append(mag)
    if not espectros:
        return 0.0

    med = np.median(np.vstack(espectros), axis=0)
    ref_pico = max(float(med.max()), 1e-12)
    db = 20 * np.log10(np.maximum(med, 1e-14) / ref_pico)
    # suaviza com borda replicada: com mode="same" o np.convolve completa as
    # pontas com zero e os ultimos bins sobem artificialmente, o que apagava
    # justamente o degrau que estamos procurando
    k = 5
    db = np.convolve(np.pad(db, k // 2, mode="edge"), np.ones(k) / k, mode="valid")

    nyquist = taxa / 2.0
    freq = np.linspace(0.0, nyquist, len(db))

    banda_ref = (freq >= 2000) & (freq <= 5000)
    topo = freq >= nyquist * 0.95
    if banda_ref.sum() < 4 or topo.sum() < 4:
        return nyquist / 1000.0

    nivel_ref = float(np.mean(db[banda_ref]))
    nivel_topo = float(np.mean(db[topo]))
    queda = nivel_ref - nivel_topo

    # sem degrau: o arquivo usa a banda toda
    if queda < 35.0:
        return nyquist / 1000.0

    limiar = nivel_topo + queda * 0.5
    acima = np.where(db > limiar)[0]
    if len(acima) == 0:
        return 0.0
    return float(freq[int(acima[-1])]) / 1000.0


def analisar(caminho: Path, amostra_segundos: int = 150) -> Diagnostico:
    """Le o arquivo, mede e classifica. Nao modifica nada."""
    d = Diagnostico(arquivo=caminho)
    try:
        _ler_cabecalho(caminho, d)
    except Exception as e:
        log.warning("Cabecalho ilegivel em %s: %s", caminho.name, e)

    try:
        with sf.SoundFile(str(caminho)) as f:
            d.taxa = d.taxa or f.samplerate
            d.canais = d.canais or f.channels
            d.duracao = d.duracao or (len(f) / f.samplerate)
            f.seek(min(len(f) // 4, max(0, len(f) - f.samplerate * amostra_segundos)))
            x = f.read(f.samplerate * amostra_segundos, dtype="float32", always_2d=True)
    except Exception:
        # formatos que o libsndfile nao abre (mp3 antigo, m4a, video): decodifica
        from .canais import decodificar_para_wav
        tmp = decodificar_para_wav(caminho, estereo=False)
        with sf.SoundFile(str(tmp)) as f:
            d.taxa = f.samplerate
            d.canais = f.channels
            d.duracao = len(f) / f.samplerate
            f.seek(min(len(f) // 4, max(0, len(f) - f.samplerate * amostra_segundos)))
            x = f.read(f.samplerate * amostra_segundos, dtype="float32", always_2d=True)

    if x.size == 0:
        d.classe = "RUIM"
        d.notas.append("arquivo vazio ou ilegivel")
        return d

    d.corte_khz = _corte_de_banda(x, d.taxa)
    d.clipping_pct = float(np.mean(np.abs(x) >= 0.999) * 100)

    if x.shape[1] >= 2:
        l, r = x[:, 0], x[:, 1]
        if l.std() > 1e-9 and r.std() > 1e-9:
            d.correlacao = float(np.corrcoef(l, r)[0, 1])
        else:
            d.correlacao = 1.0

    # Limiares medidos com LAME: 320k corta perto de 20.2 kHz, 192k perto de
    # 18.9, 128k perto de 16.8 e 96k perto de 15.4.
    if d.canais == 1 or (d.canais >= 2 and d.correlacao > 0.9995):
        d.classe = "RUIM"
        d.notas.append("mono ou estereo falso: a separacao perde muita qualidade")
    elif d.corte_khz >= 19.5:
        d.classe = "BOM"
    elif d.corte_khz >= 17.5:
        d.classe = "MEDIO"
        d.notas.append(f"banda ate {d.corte_khz:.1f} kHz: equivale a um MP3 de 192 kbps")
    else:
        d.classe = "RUIM"
        d.notas.append(f"banda ate {d.corte_khz:.1f} kHz: equivale a 128 kbps ou menos")

    if d.bitrate_kbps >= 256 and d.corte_khz and d.corte_khz < 19.0:
        d.notas.append("o cabecalho diz alta taxa mas o espectro diz o contrario: "
                       "arquivo transcodificado e reetiquetado")
    if d.clipping_pct > 0.5:
        d.notas.append(f"clipping em {d.clipping_pct:.1f}% das amostras")
    if d.canais == 6:
        d.notas.append("5.1 detectado: da para tirar o canal central")
    if d.taxa and d.taxa < 44100:
        d.notas.append(f"taxa baixa ({d.taxa} Hz)")

    return d
