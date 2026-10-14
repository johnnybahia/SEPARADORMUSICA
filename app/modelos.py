"""Resolucao de modelos.

O catalogo do audio-separator muda entre versoes e os nomes misturam
hifen, sublinhado e ponto sem padrao. Por isso nada e fixo no codigo:
cada papel tem uma lista de candidatos em ordem de preferencia e o
resolvedor casa por forma normalizada, ignorando separadores.

Rode verificar_modelos.bat para ver o catalogo completo desta maquina.
"""
from __future__ import annotations

import functools
import re

from .util import PASTA_MODELOS, log

EXTENSOES = (".ckpt", ".onnx", ".pth", ".yaml", ".th")
# ckpt e onnx sao pesos de verdade; yaml so vale para os modelos Demucs
PESO_EXTENSAO = {".ckpt": 0, ".onnx": 1, ".pth": 2, ".th": 3, ".yaml": 4}

# Ordem de preferencia por papel. Casa por trecho, sem diferenciar
# maiuscula nem separador: "bs-roformer-sw" acha "BS_Roformer_SW".
CANDIDATOS: dict[str, list[str]] = {
    # voz x instrumental, melhor qualidade disponivel
    "voz_qualidade": [
        "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "model_bs_roformer_ep_368_sdr_12.9628.ckpt",
        "melband_roformer_inst_v1e_plus.ckpt",
        "melband_roformer_inst_v1e.ckpt",
        "MDX23C-8KFFT-InstVoc_HQ.ckpt",
        "UVR-MDX-NET-Inst_HQ_4.onnx",
    ],
    # voz x instrumental leve, para CPU fraca
    "voz_rapido": [
        "UVR-MDX-NET-Inst_HQ_4.onnx",
        "UVR-MDX-NET-Inst_HQ_3.onnx",
        "UVR_MDXNET_Main.onnx",
        "UVR-MDX-NET-Voc_FT.onnx",
        "htdemucs.yaml",
    ],
    # multipista de alta qualidade: 6 faixas de uma vez, bem melhor que
    # o htdemucs_6s em todas as faixas. Pesado: exige GPU ou CPU boa.
    "stems_qualidade": [
        "BS-Roformer-SW.ckpt",
        "bs_roformer_sw",
        "htdemucs_ft.yaml",
    ],
    # 4 faixas rapido: voz, bateria, baixo, outros
    "stems4": [
        "htdemucs_ft.yaml",
        "htdemucs.yaml",
        "hdemucs_mmi.yaml",
    ],
    # 4 faixas leve: o htdemucs_ft roda quatro redes especializadas, uma por
    # faixa, e custa cerca de 4x o htdemucs simples para um ganho modesto.
    # Em maquina sem GPU o simples e a escolha certa.
    "stems4_rapido": [
        "htdemucs.yaml",
        "hdemucs_mmi.yaml",
        "htdemucs_ft.yaml",
    ],
    # 6 faixas leve, quando a maquina nao aguenta o Roformer
    "stems6": [
        "htdemucs_6s.yaml",
    ],
    # voz masculina x feminina, para dueto. SDR baixo (7.29): funciona bem
    # quando cada um canta um trecho, mal quando cantam juntos, e nao ha
    # solucao quando os dois timbres sao parecidos.
    "vozes": [
        "bs_roformer_male_female_by_aufr33_sdr_7.2889.ckpt",
        "male_female",
    ],
    # bateria em pecas
    "drumsep": [
        "MDX23C-DrumSep-aufr33-jarredou.ckpt",
        "drumsep",
    ],
    # voz principal x coro
    "backing": [
        "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
        "mel_band_roformer_karaoke_gabox.ckpt",
        "bs_roformer_karaoke_anvuew.ckpt",
        "UVR_MDXNET_KARA_2.onnx",
        "6_HP-Karaoke-UVR.pth",
    ],
}


def _normalizar(nome: str) -> str:
    """Tira separadores para o casamento nao depender de - _ ou espaco."""
    return re.sub(r"[-_.\s]+", "", nome.lower())


def _e_modelo_util(nome: str) -> bool:
    """Descarta URLs e arquivos de configuracao que nao se carregam."""
    if "://" in nome:
        return False
    baixo = nome.lower()
    if not baixo.endswith(EXTENSOES):
        return False
    if baixo.startswith("config_") or "_config." in baixo:
        return False
    return True


@functools.lru_cache(maxsize=1)
def catalogo() -> list[str]:
    """Nomes de arquivo de modelo realmente carregaveis, sem configs."""
    from audio_separator.separator import Separator

    sep = Separator(model_file_dir=str(PASTA_MODELOS), log_level=40)
    bruto = sep.list_supported_model_files()
    achados: set[str] = set()

    def andar(no):
        if isinstance(no, str):
            if _e_modelo_util(no):
                achados.add(no)
        elif isinstance(no, dict):
            for k, v in no.items():
                if isinstance(k, str) and _e_modelo_util(k):
                    achados.add(k)
                andar(v)
        elif isinstance(no, (list, tuple, set)):
            for v in no:
                andar(v)

    andar(bruto)
    return sorted(achados)


def _melhor(nomes: list[str]) -> str:
    """Entre varios nomes que casaram, prefere peso real a yaml."""
    return sorted(nomes, key=lambda m: (PESO_EXTENSAO.get(m[m.rfind("."):].lower(), 9),
                                        len(m), m))[0]


def resolver(papel: str, obrigatorio: bool = True) -> str | None:
    """Primeiro candidato do papel que exista no catalogo."""
    disponiveis = catalogo()
    mapa = {}
    for m in disponiveis:
        mapa.setdefault(_normalizar(m), []).append(m)

    for cand in CANDIDATOS.get(papel, []):
        alvo = _normalizar(cand)
        if alvo in mapa:                       # nome exato
            return _melhor(mapa[alvo])
        parciais = [m for chave, nomes in mapa.items() if alvo in chave
                    for m in nomes]
        if parciais:                           # trecho do nome
            return _melhor(parciais)

    if obrigatorio:
        raise RuntimeError(
            f"Nenhum modelo encontrado para o papel '{papel}'.\n"
            f"Rode verificar_modelos.bat e ajuste CANDIDATOS em app/modelos.py."
        )
    log.warning("Papel opcional '%s' sem modelo disponivel.", papel)
    return None


def relatorio() -> str:
    linhas = ["Papeis resolvidos nesta instalacao:", ""]
    for papel in CANDIDATOS:
        try:
            achado = resolver(papel, obrigatorio=False) or "NAO ENCONTRADO"
        except Exception as e:
            achado = f"erro: {e}"
        linhas.append(f"  {papel:<16} -> {achado}")
    cat = catalogo()
    linhas += ["", f"Catalogo completo ({len(cat)} modelos carregaveis):", ""]
    linhas += [f"  {m}" for m in cat]
    return "\n".join(linhas)


if __name__ == "__main__":
    print(relatorio())
