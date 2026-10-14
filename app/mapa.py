"""Mapa de atividade das faixas.

Mostra onde cada instrumento toca ao longo da musica, em faixas coloridas
empilhadas. Responde de relance o que o espectro nao responde: onde entra a
voz, onde esta o solo de guitarra, quando o baixo para.

O calculo le cada stem uma vez em blocos, mede a energia a cada 0,2 s e
guarda o resultado num mapa.json dentro da pasta da musica. Depois disso e
instantaneo.

Cada faixa e normalizada por si mesma, nao pelo conjunto: o objetivo e
enxergar *quando* ela toca, nao o quanto ela e alta em relacao as outras.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import soundfile as sf

from .util import PASTA_SAIDA, log

JANELA_S = 0.2          # resolucao do mapa
VERSAO = 2              # muda quando o formato do mapa.json muda

# Cores por tipo de faixa. Familia de instrumento com matiz proximo, para o
# olho agrupar bateria com bateria e voz com voz.
CORES = {
    "vocal": "#ff8a2b", "vocal_principal": "#ff8a2b", "coro": "#ffb570",
    "voz_masculina": "#ff7a3d", "voz_feminina": "#ffa85c",
    "vocal_outros": "#d99a6c",
    "instrumental": "#7e8b99",
    "bateria": "#e2574c", "bumbo": "#c94a3f", "caixa": "#e2574c",
    "toms": "#e07a55", "chimbal": "#f0a08a", "pratos": "#f5b8a0",
    "conducao": "#efa890", "crash": "#f7c4ae", "bateria_outros": "#d98878",
    "baixo": "#6f8fe0", "guitarra": "#28d1c4", "piano": "#b79ae4",
    "outros": "#8fbf6a",
}
CORES_RESERVA = ["#5fa8d3", "#c9a227", "#9d7ad9", "#6ec6a8", "#d98cb3"]


def cor_de(chave: str, ordem: int) -> str:
    return CORES.get(chave, CORES_RESERVA[ordem % len(CORES_RESERVA)])


def _envelope(caminho: Path, janela_s: float = JANELA_S) -> tuple[list, float]:
    """Energia da faixa a cada janela, normalizada por ela mesma."""
    with sf.SoundFile(str(caminho)) as f:
        taxa = f.samplerate
        total = len(f)
        passo = max(1, int(taxa * janela_s))
        niveis = []
        while True:
            x = f.read(passo, dtype="float32", always_2d=True)
            if len(x) == 0:
                break
            niveis.append(float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))))
    duracao = total / taxa if taxa else 0.0
    if not niveis:
        return [], duracao

    arr = np.asarray(niveis, dtype=np.float64)
    pico = float(arr.max())
    if pico <= 1e-9:
        return [0.0] * len(arr), duracao

    # escala em dB: em linear, tudo que nao e refrao vira quase zero
    db = 20 * np.log10(np.maximum(arr / pico, 1e-6))
    v = np.clip((db + 45.0) / 45.0, 0.0, 1.0)
    return [round(float(x), 3) for x in v], duracao


def calcular(titulo: str, canais: list[dict]) -> dict:
    """Mapa da musica. Usa o cache quando as faixas nao mudaram."""
    pasta = PASTA_SAIDA / titulo
    cache = pasta / "mapa.json"

    try:
        assinatura = "|".join(
            f"{Path(c['arquivo']).name}:{(PASTA_SAIDA / c['arquivo']).stat().st_size}"
            for c in canais)
    except Exception:
        assinatura = ""

    if cache.exists():
        try:
            guardado = json.loads(cache.read_text(encoding="utf-8"))
            if (guardado.get("assinatura") == assinatura
                    and guardado.get("versao") == VERSAO):
                return guardado
        except Exception as e:
            log.debug("mapa.json ilegivel em %s: %s", titulo, e)

    saida, duracao = [], 0.0
    for i, c in enumerate(canais):
        caminho = PASTA_SAIDA / c["arquivo"]
        try:
            niveis, dur = _envelope(caminho)
            duracao = max(duracao, dur)
        except Exception as e:
            log.warning("Nao consegui medir %s: %s", caminho.name, e)
            niveis = []
        saida.append({"chave": c["chave"], "nome": c["nome"],
                      "cor": cor_de(c["chave"], i), "niveis": niveis})

    mapa = {"versao": VERSAO, "assinatura": assinatura, "janela": JANELA_S,
            "duracao": round(duracao, 2), "canais": saida}
    try:
        cache.write_text(json.dumps(mapa, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.debug("Nao gravei o mapa.json: %s", e)
    return mapa
