"""Mixagens para treino e karaoke.

Regra de projeto: o stem que voce vai tocar nao e mutado, e atenuado.
Mute total expoe o vazamento do modelo e atrapalha mais do que ajuda.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .util import log, rodar_ffmpeg

# stems que nunca entram numa base instrumental
VOCAIS = {"vocal", "vocal_principal", "coro"}
# pecas de bateria: se existirem, a bateria inteira sai do mix para nao dobrar
PECAS_BATERIA = {"bumbo", "caixa", "toms", "pratos", "chimbal", "conducao", "crash"}


def _mixar(entradas: list[tuple[Path, float]], destino: Path) -> Path:
    """Soma arquivos com ganho individual em dB. Ganho -120 significa mudo."""
    entradas = [(p, g) for p, g in entradas if g > -119]
    if not entradas:
        raise ValueError("nada para mixar")
    destino.parent.mkdir(parents=True, exist_ok=True)

    args: list[str] = []
    for caminho, _ in entradas:
        args += ["-i", str(caminho)]

    partes, rotulos = [], []
    for i, (_, ganho) in enumerate(entradas):
        partes.append(f"[{i}:a]volume={ganho:.2f}dB[a{i}]")
        rotulos.append(f"[a{i}]")
    filtro = (";".join(partes) + ";" + "".join(rotulos)
              + f"amix=inputs={len(entradas)}:normalize=0:dropout_transition=0[mix]")

    args += ["-filter_complex", filtro, "-map", "[mix]", "-c:a", "pcm_f32le", str(destino)]
    rodar_ffmpeg(args)
    return destino


def _relevantes(stems: dict[str, Path]) -> dict[str, Path]:
    """Evita somar bateria inteira junto com as pecas dela."""
    uteis = dict(stems)
    if PECAS_BATERIA & set(uteis):
        uteis.pop("bateria", None)
    if "vocal_principal" in uteis and "coro" in uteis:
        uteis.pop("vocal", None)
    return uteis


def instrumental(stems: dict[str, Path], destino: Path,
                 manter_coro: bool = False) -> Path | None:
    """Base de karaoke. manter_coro deixa o backing vocal na mistura."""
    uteis = _relevantes(stems)
    if "instrumental" in uteis and not manter_coro:
        return uteis["instrumental"]
    fora = VOCAIS - ({"coro"} if manter_coro else set())
    entradas = [(p, 0.0) for n, p in uteis.items()
                if n not in fora and n != "instrumental"]
    if not entradas:
        return None
    return _mixar(entradas, destino)


def minus_one(stems: dict[str, Path], alvo: str, destino: Path,
              atenuacao_db: float = -15.0, com_voz: bool = False) -> Path | None:
    """Base para treinar 'alvo'.

    atenuacao_db = -120 vira mute total.
    """
    uteis = _relevantes(stems)
    if alvo not in uteis:
        log.warning("Stem '%s' nao existe nesta musica.", alvo)
        return None
    entradas = []
    for nome, caminho in uteis.items():
        if nome == "instrumental":
            continue
        if nome in VOCAIS and not com_voz:
            continue
        entradas.append((caminho, atenuacao_db if nome == alvo else 0.0))
    if not entradas:
        return None
    return _mixar(entradas, destino)


def transpor(origem: Path, destino: Path, semitons: float,
             taxa: int = 44100) -> Path:
    """Muda o tom sem mudar a duracao, via asetrate + atempo.

    Acima de 5 semitons o timbre fica artificial. O limite e proposital.
    """
    semitons = max(-5.0, min(5.0, float(semitons)))
    if abs(semitons) < 0.01:
        rodar_ffmpeg(["-i", str(origem), "-c:a", "pcm_f32le", str(destino)])
        return destino
    fator = 2 ** (semitons / 12.0)
    nova_taxa = int(round(taxa * fator))
    # atempo aceita 0.5 a 2.0 por etapa; 5 semitons cabe em uma so
    compensa = 1.0 / fator
    cadeia = f"asetrate={nova_taxa},aresample={taxa},atempo={compensa:.6f}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    rodar_ffmpeg(["-i", str(origem), "-af", cadeia, "-c:a", "pcm_f32le", str(destino)])
    return destino


def medir(origem: Path, bloco: int = 1 << 20) -> dict:
    """Pico e RMS do arquivo, em dBFS. Le em blocos, nao carrega tudo."""
    import soundfile as sf

    pico = 0.0
    soma = 0.0
    n = 0
    with sf.SoundFile(str(origem)) as f:
        while True:
            x = f.read(bloco, dtype="float32", always_2d=True)
            if len(x) == 0:
                break
            pico = max(pico, float(np.abs(x).max()))
            soma += float(np.sum(np.square(x, dtype=np.float64)))
            n += x.size
    rms = (soma / n) ** 0.5 if n else 0.0
    return {
        "pico_db": 20 * np.log10(max(pico, 1e-12)),
        "rms_db": 20 * np.log10(max(rms, 1e-12)),
        "pico": pico,
    }


def reforcar(origem: Path, destino: Path, modo: str = "pico",
             alvo_db: float = -1.0, ganho_manual_db: float = 6.0,
             bloco: int = 1 << 20) -> tuple[Path, str]:
    """Levanta o volume de uma faixa isolada.

    Uma faixa de baixo sai baixa porque ela e baixa dentro da mistura: o
    modelo nao mudou o nivel, so tirou o resto. Levantar exige ganho.

    modo:
      pico   - o maior ganho possivel sem distorcer (seguro, sem limitador)
      rms    - deixa o volume percebido no alvo, com limitador suave nos picos
      manual - aplica o ganho que voce pedir, com limitador

    NAO use as faixas reforcadas no mixer: normalizar cada faixa em separado
    destroi o equilibrio entre elas. Isso e para ouvir ou estudar a faixa
    sozinha.
    """
    import soundfile as sf

    m = medir(origem)
    if modo == "pico":
        ganho_db = float(alvo_db) - m["pico_db"]
        limitar = False
    elif modo == "rms":
        ganho_db = float(alvo_db) - m["rms_db"]
        limitar = True
    else:
        ganho_db = float(ganho_manual_db)
        limitar = True

    ganho = float(10 ** (ganho_db / 20.0))
    teto = float(10 ** (-1.0 / 20.0))      # -1 dBFS
    destino.parent.mkdir(parents=True, exist_ok=True)

    picos_tocados = 0
    total = 0
    with sf.SoundFile(str(origem)) as fin:
        with sf.SoundFile(str(destino), "w", samplerate=fin.samplerate,
                          channels=fin.channels, subtype="FLOAT") as fout:
            while True:
                x = fin.read(bloco, dtype="float32", always_2d=True)
                if len(x) == 0:
                    break
                y = x * ganho
                if limitar:
                    excede = np.abs(y) > teto
                    picos_tocados += int(excede.sum())
                    # joelho suave: comprime so o que passa do teto
                    y = np.where(excede,
                                 np.sign(y) * (teto + (np.abs(y) - teto)
                                               / (1.0 + (np.abs(y) - teto) * 8.0)),
                                 y)
                    y = np.clip(y, -1.0, 1.0)
                total += y.size
                fout.write(y)

    relato = (f"nivel original: pico {m['pico_db']:.1f} dBFS, "
              f"RMS {m['rms_db']:.1f} dBFS\n\n"
              f"ganho aplicado: **{ganho_db:+.1f} dB**")
    if limitar and total:
        pct = 100.0 * picos_tocados / total
        relato += f"\n\nlimitador atuou em {pct:.2f}% das amostras"
        if pct > 2.0:
            relato += (" — acima de 2% comeca a achatar o som; "
                       "use um alvo mais baixo ou o modo pico")
    return destino, relato
    """Encode final unico. FLAC para arquivo, MP3 320 para tocar em qualquer lugar."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    formato = formato.upper()
    if formato == "MP3":
        args = ["-i", str(origem), "-c:a", "libmp3lame", "-b:a", "320k", str(destino)]
    elif formato == "FLAC":
        args = ["-i", str(origem), "-c:a", "flac", "-compression_level", "5", str(destino)]
    elif formato == "WAV":
        args = ["-i", str(origem), "-c:a", "pcm_s24le", str(destino)]
    else:
        raise ValueError(f"formato desconhecido: {formato}")
    rodar_ffmpeg(args)
    return destino
