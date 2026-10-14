"""Operacoes de canal, todas sem IA e quase instantaneas.

- decodificacao para WAV float sem perda
- separacao L, R, mid e side
- karaoke por cancelamento de fase (L-R)
- 5.1: extrair o canal central (voz) ou remove-lo (karaoke perfeito)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from .util import PASTA_TRABALHO, hash_curto, log, rodar_ffmpeg


def pasta_de_trabalho(origem: Path) -> Path:
    p = PASTA_TRABALHO / hash_curto(origem)
    p.mkdir(parents=True, exist_ok=True)
    return p


def decodificar_para_wav(origem: Path, estereo: bool = True,
                         destino: Path | None = None) -> Path:
    """Decodifica para WAV float 32 sem reamostrar.

    estereo=False preserva o numero original de canais, necessario para 5.1.
    """
    trab = pasta_de_trabalho(origem)
    if destino is None:
        destino = trab / ("entrada.wav" if estereo else "entrada_multicanal.wav")
    if destino.exists() and destino.stat().st_size > 1024:
        return destino
    args = ["-i", str(origem), "-vn", "-map", "a:0", "-c:a", "pcm_f32le"]
    if estereo:
        args += ["-ac", "2"]
    args += [str(destino)]
    rodar_ffmpeg(args)
    return destino


def contar_canais(origem: Path) -> int:
    try:
        with sf.SoundFile(str(origem)) as f:
            return f.channels
    except Exception:
        wav = decodificar_para_wav(origem, estereo=False)
        with sf.SoundFile(str(wav)) as f:
            return f.channels


def preparar_entrada(origem: Path) -> tuple[Path, dict]:
    """Devolve o WAV estereo que alimenta os modelos, mais informacoes uteis."""
    multi = decodificar_para_wav(origem, estereo=False)
    canais = contar_canais(multi)
    info = {"canais_originais": canais, "tem_51": canais == 6, "central_real": False}

    if canais == 6:
        info["central_real"] = _central_e_real(multi)

    trab = pasta_de_trabalho(origem)
    estereo = trab / "entrada.wav"
    if not estereo.exists():
        if canais == 6:
            # downmix padrao ITU preservando a voz do central
            rodar_ffmpeg(["-i", str(multi), "-ac", "2", "-c:a", "pcm_f32le", str(estereo)])
        elif canais == 1:
            rodar_ffmpeg(["-i", str(multi), "-af", "pan=stereo|c0=c0|c1=c0",
                          "-c:a", "pcm_f32le", str(estereo)])
        elif canais == 2:
            estereo = multi if multi.name == "entrada.wav" else estereo
            if not estereo.exists():
                rodar_ffmpeg(["-i", str(multi), "-c:a", "pcm_f32le", str(estereo)])
        else:
            rodar_ffmpeg(["-i", str(multi), "-ac", "2", "-c:a", "pcm_f32le", str(estereo)])
    return estereo, info


def _central_e_real(wav51: Path, segundos: int = 60) -> bool:
    """Distingue 5.1 de verdade de upmix falso.

    Em upmix falso o central e apenas (L+R), entao a correlacao com a soma
    dos frontais fica praticamente em 1.
    """
    try:
        with sf.SoundFile(str(wav51)) as f:
            f.seek(min(len(f) // 3, max(0, len(f) - f.samplerate * segundos)))
            x = f.read(f.samplerate * segundos, dtype="float32", always_2d=True)
        if x.shape[1] < 3:
            return False
        fc = x[:, 2]
        soma = x[:, 0] + x[:, 1]
        if fc.std() < 1e-6:
            return False
        if soma.std() < 1e-9:
            return True
        c = abs(float(np.corrcoef(fc, soma)[0, 1]))
        return c < 0.98
    except Exception as e:
        log.warning("Nao consegui avaliar o canal central: %s", e)
        return False


def extrair_51(origem: Path, destino_dir: Path) -> dict[str, Path]:
    """Karaoke e a cappella a partir de 5.1 real. Sem IA, sem artefato."""
    multi = decodificar_para_wav(origem, estereo=False)
    if contar_canais(multi) != 6:
        return {}
    destino_dir.mkdir(parents=True, exist_ok=True)
    saidas = {}

    voz = destino_dir / "51_vocal.wav"
    rodar_ffmpeg(["-i", str(multi), "-af", "pan=mono|c0=FC",
                  "-c:a", "pcm_f32le", str(voz)])
    saidas["vocal_51"] = voz

    inst = destino_dir / "51_instrumental.wav"
    rodar_ffmpeg([
        "-i", str(multi), "-af",
        "pan=stereo|c0=FL+0.7*BL+0.5*LFE|c1=FR+0.7*BR+0.5*LFE",
        "-c:a", "pcm_f32le", str(inst),
    ])
    saidas["instrumental_51"] = inst
    return saidas


def cancelamento_de_fase(entrada_wav: Path, destino: Path) -> Path:
    """Karaoke instantaneo por L-R. Referencia rapida, nao resultado final."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    rodar_ffmpeg(["-i", str(entrada_wav), "-af",
                  "pan=stereo|c0=0.5*c0-0.5*c1|c1=0.5*c0-0.5*c1",
                  "-c:a", "pcm_f32le", str(destino)])
    return destino


def separar_lados(entrada_wav: Path, destino_dir: Path) -> dict[str, Path]:
    """Gera L, R, mid e side como arquivos independentes."""
    destino_dir.mkdir(parents=True, exist_ok=True)
    receitas = {
        "canal_L": "pan=mono|c0=c0",
        "canal_R": "pan=mono|c0=c1",
        "mid": "pan=mono|c0=0.5*c0+0.5*c1",
        "side": "pan=mono|c0=0.5*c0-0.5*c1",
    }
    saidas = {}
    for nome, filtro in receitas.items():
        alvo = destino_dir / f"{nome}.wav"
        rodar_ffmpeg(["-i", str(entrada_wav), "-af", filtro,
                      "-c:a", "pcm_f32le", str(alvo)])
        saidas[nome] = alvo
    return saidas
