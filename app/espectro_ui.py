"""Aba do editor espectral.

Selecao por numeros, nao por desenho: funciona em qualquer versao do
Gradio e em qualquer maquina. A regiao escolhida aparece realcada sobre
o espectrograma, entao voce enxerga o que marcou antes de aplicar.

A previa processa so a janela visivel (segundos), o que leva menos de um
segundo. A exportacao processa a musica inteira em blocos.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from . import espectro as E
from .separador import INSTRUMENTOS
from .util import PASTA_SAIDA, log, nome_unico

# faixas que servem como referencia de posse; bases e mixagens ficam de fora
NOMES_STEM = set(INSTRUMENTOS) | {"vocal", "vocal_principal", "coro", "instrumental"}
AUDIO_EXT = {".flac", ".mp3", ".wav", ".ogg", ".m4a"}


def listar_musicas() -> list[str]:
    if not PASTA_SAIDA.exists():
        return []
    return sorted(p.name for p in PASTA_SAIDA.iterdir() if p.is_dir())


def stems_da_musica(nome_pasta: str) -> dict[str, Path]:
    pasta = PASTA_SAIDA / (nome_pasta or "")
    if not pasta.is_dir():
        return {}
    achados = {}
    for p in sorted(pasta.iterdir()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXT:
            if p.stem.lower() in NOMES_STEM:
                achados[p.stem.lower()] = p
    return achados


def _outros(stems: dict[str, Path], alvo: str) -> list[Path]:
    """Referencias de posse: tudo menos o alvo e as mixagens derivadas."""
    fora = {alvo, "instrumental"}
    if "vocal_principal" in stems and "vocal" in stems:
        fora.add("vocal")
    return [p for n, p in stems.items() if n not in fora]


def _para_gradio(y: np.ndarray, taxa: int):
    """(amostras, canais) float -> formato que o gr.Audio aceita."""
    y = np.clip(y, -1.0, 1.0)
    return taxa, (y * 32767).astype(np.int16)


# --------------------------------------------------------------------------- #
#  Construcao da mascara conforme a escolha do usuario
# --------------------------------------------------------------------------- #
def _mascara(forma, taxa, tipo, janela_ini, t_rel_ini, t_rel_fim,
             f_ini, f_fim, f0, n_harm):
    t_ini = janela_ini + float(t_rel_ini)
    t_fim = janela_ini + float(t_rel_fim)
    if tipo.startswith("Harm"):
        return E.mascara_harmonicos(forma, taxa, float(f0), int(n_harm),
                                    t_ini=t_ini, t_fim=t_fim,
                                    desloc_s=janela_ini)
    return E.mascara_retangulo(forma, taxa, t_ini, t_fim,
                               float(f_ini), float(f_fim), desloc_s=janela_ini)


# --------------------------------------------------------------------------- #
#  Acoes
# --------------------------------------------------------------------------- #
def ver(musica, stem, ini, dur, tipo, t0, t1, f0_hz, f1_hz, fund, nharm):
    """Desenha o espectrograma da janela com a selecao realcada."""
    try:
        return _ver(musica, stem, ini, dur, tipo, t0, t1, f0_hz, f1_hz,
                    fund, nharm)
    except Exception as e:
        log.error("Falha ao desenhar o espectrograma: %s", e, exc_info=True)
        return None, (f"**Nao consegui desenhar o espectrograma.**\n\n`{e}`\n\n"
                      f"Se a mensagem citar formato de arquivo, a faixa foi "
                      f"exportada em MP3 e o leitor desta maquina nao abre. "
                      f"Separe de novo escolhendo FLAC ou WAV.")


def _ver(musica, stem, ini, dur, tipo, t0, t1, f0_hz, f1_hz, fund, nharm):
    if not musica:
        return None, "Escolha uma musica na lista acima."
    stems = stems_da_musica(musica)
    if not stems:
        return None, (f"A pasta `{musica}` nao tem faixas reconhecidas.\n\n"
                      f"O editor procura arquivos chamados vocal, baixo, "
                      f"bateria, guitarra, piano, outros e coro.")
    if not stem:
        return None, ("Escolha uma faixa. Disponiveis: "
                      + ", ".join(f"`{n}`" for n in sorted(stems)))
    if stem not in stems:
        return None, (f"A faixa `{stem}` nao existe nessa musica. "
                      f"Disponiveis: " + ", ".join(f"`{n}`" for n in sorted(stems)))

    alvo = stems[stem]
    total = E.duracao(alvo)
    ini = max(0.0, min(float(ini), max(0.0, total - 1)))
    dur = max(2.0, min(float(dur), 30.0, total - ini))

    tr = E.ler_trecho(alvo, ini, dur)
    X = tr.espectros[0]
    m = _mascara(X.shape, tr.taxa, tipo, ini, t0, t1, f0_hz, f1_hz, fund, nharm)
    img = E.imagem_espectro(X, tr.taxa, destaque=m)

    marcados = int((m > 0.5).sum())
    pct = 100.0 * marcados / m.size
    info = (f"**{stem}** · musica de {total/60:.0f}:{int(total)%60:02d} · "
            f"janela {ini:.0f}s a {ini+dur:.0f}s · "
            f"selecao cobre {pct:.1f}% do espectro visivel\n\n"
            f"Eixo horizontal: tempo. Vertical: frequencia em escala "
            f"logaritmica, grave embaixo. A area azulada e a sua selecao.")
    return img, info


def previa(musica, stem, ini, dur, tipo, t0, t1, f0_hz, f1_hz, fund, nharm,
           operacao, ganho, usar_posse):
    """Aplica a operacao so na janela visivel e devolve antes e depois."""
    stems = stems_da_musica(musica)
    if stem not in stems:
        return None, None, "Escolha uma faixa valida."

    alvo = stems[stem]
    total = E.duracao(alvo)
    ini = max(0.0, min(float(ini), max(0.0, total - 1)))
    dur = max(2.0, min(float(dur), 30.0, total - ini))

    tr = E.ler_trecho(alvo, ini, dur)
    outros = _outros(stems, stem)

    mags_por_canal = []
    for c in range(len(tr.canais)):
        mags = []
        for o in outros:
            try:
                t2 = E.ler_trecho(o, ini, dur)
                cc = min(c, len(t2.espectros) - 1)
                if t2.espectros[cc].shape == tr.espectros[c].shape:
                    mags.append(np.abs(t2.espectros[cc]))
            except Exception as e:
                log.warning("Referencia %s ignorada: %s", o.name, e)
        mags_por_canal.append(mags)

    antes = np.stack(tr.canais, axis=1)
    saidas = []
    for c, X in enumerate(tr.espectros):
        m = _mascara(X.shape, tr.taxa, tipo, ini, t0, t1, f0_hz, f1_hz, fund, nharm)
        Xm = E.aplicar(X, mags_por_canal[c], m, operacao_curta(operacao),
                       float(ganho), bool(usar_posse))
        saidas.append(E.istft(Xm, len(tr.canais[c])))
    depois = np.stack(saidas, axis=1)

    dif = 20 * np.log10(max(np.sqrt(np.mean((depois - antes) ** 2)), 1e-12) /
                        max(np.sqrt(np.mean(antes ** 2)), 1e-12))
    txt = (f"Diferenca entre antes e depois: {dif:.1f} dB.\n\n"
           f"Se ficou perto de -60 dB, a selecao nao pegou nada. "
           f"Se passou de -6 dB, mexeu em muita coisa.")
    return _para_gradio(antes, tr.taxa), _para_gradio(depois, tr.taxa), txt


def operacao_curta(rotulo: str) -> str:
    if rotulo.startswith("Isolar"):
        return "isolar"
    if rotulo.startswith("Real"):
        return "realcar"
    return "atenuar"


def exportar(musica, stem, tipo, ini, dur, t0, t1, f0_hz, f1_hz, fund, nharm,
             operacao, ganho, usar_posse, progresso=None):
    """Aplica na musica inteira, em blocos."""
    stems = stems_da_musica(musica)
    if stem not in stems:
        return "Escolha uma faixa valida.", None

    alvo = stems[stem]
    outros = _outros(stems, stem)
    op_curta = operacao_curta(operacao)

    def passo(X, mags, taxa, inicio_bloco):
        m = _mascara(X.shape, taxa, tipo, ini, t0, t1, f0_hz, f1_hz, fund, nharm)
        # a mascara e calculada em tempo absoluto; desloca para o bloco
        m2 = E.mascara_retangulo(X.shape, taxa,
                                 ini + float(t0), ini + float(t1),
                                 float(f0_hz), float(f1_hz),
                                 desloc_s=inicio_bloco) \
            if not tipo.startswith("Harm") else \
            E.mascara_harmonicos(X.shape, taxa, float(fund), int(nharm),
                                 t_ini=ini + float(t0), t_fim=ini + float(t1),
                                 desloc_s=inicio_bloco)
        return E.aplicar(X, mags, m2, op_curta, float(ganho), bool(usar_posse))

    destino = nome_unico(alvo.parent / f"{stem}_editado.wav")
    E.processar_arquivo(alvo, outros, destino, passo, progresso)
    return f"Gerado: `{destino}`", str(destino)


def limpar(musica, stem, forca, piso_db, progresso=None):
    """Limpeza automatica de vazamento na musica inteira. Sem selecao."""
    stems = stems_da_musica(musica)
    if stem not in stems:
        return "Escolha uma faixa valida.", None
    alvo = stems[stem]
    outros = _outros(stems, stem)
    if not outros:
        return ("Essa musica so tem uma faixa. A limpeza precisa das outras "
                "para saber o que e vazamento."), None

    def passo(X, mags, taxa, inicio_bloco):
        if not mags:
            return X
        return E.limpar_vazamento(X, mags, float(forca), float(piso_db))

    destino = nome_unico(alvo.parent / f"{stem}_limpo.wav")
    E.processar_arquivo(alvo, outros, destino, passo, progresso)
    return (f"Gerado: `{destino}`\n\nCompare com o original antes de "
            f"substituir. Forca alta deixa o som fino."), str(destino)
