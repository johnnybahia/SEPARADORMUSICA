"""Deteccao de hardware, escolha de perfil e bloqueio de suspensao do Windows."""
from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

from .util import log


@dataclass
class Perfil:
    nome: str
    gpu: bool
    nome_gpu: str
    vram_gb: float
    threads: int
    ram_gb: float
    permite_profundo: bool
    permite_seis: bool
    modelo_karaoke: str      # "qualidade" ou "rapido"
    segmento: int            # tamanho de bloco para modelos MDXC/Roformer
    aviso: str

    def resumo(self) -> str:
        acel = f"GPU {self.nome_gpu} ({self.vram_gb:.0f} GB)" if self.gpu else "CPU"
        return (f"Perfil **{self.nome}** · {acel} · {self.threads} threads · "
                f"{self.ram_gb:.0f} GB RAM")


def _detectar_gpu() -> tuple[bool, str, float]:
    try:
        import torch
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            return True, props.name, props.total_memory / (1024 ** 3)
    except Exception as e:
        log.debug("Sem CUDA: %s", e)
    return False, "", 0.0


def detectar() -> Perfil:
    gpu, nome_gpu, vram = _detectar_gpu()
    threads = os.cpu_count() or 2
    try:
        import psutil
        ram = psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        ram = 8.0

    if gpu and vram >= 8:
        return Perfil("Completo", gpu, nome_gpu, vram, threads, ram,
                      True, True, "qualidade", 256, "")
    if gpu and vram >= 4:
        return Perfil("Medio", gpu, nome_gpu, vram, threads, ram,
                      False, True, "qualidade", 128,
                      "VRAM apertada: o modo profundo fica desligado para evitar estouro.")
    if threads >= 8 and ram >= 12:
        return Perfil("CPU forte", gpu, nome_gpu, vram, threads, ram,
                      False, True, "qualidade", 128,
                      "Sem GPU: espere alguns minutos por musica.")
    # Maquina modesta: todos os modos ficam liberados, mas com os modelos
    # leves (htdemucs em vez de htdemucs_ft, que roda 4 redes e custa 4x).
    # O Profundo empilha tres passadas e passa de 15 minutos por musica:
    # segue disponivel, com o custo avisado na tela.
    return Perfil(
        "CPU leve", gpu, nome_gpu, vram, threads, ram, True, True, "rapido", 64,
        "Sem GPU: os modos usam os modelos leves. Uma musica de 4 minutos leva "
        "cerca de 5 minutos no Padrao e passa de 15 no Profundo.",
    )


_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


def impedir_suspensao() -> None:
    """Impede o Windows de dormir durante um lote longo."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED)
    except Exception as e:
        log.debug("SetThreadExecutionState falhou: %s", e)


def liberar_suspensao() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
    except Exception:
        pass
