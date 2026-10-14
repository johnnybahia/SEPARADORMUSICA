"""Editor espectral.

A ideia e a mesma do Roland R-MIX: ver a musica como energia espalhada em
tempo e frequencia e mexer so no pedaco que interessa. A diferenca e que
o R-MIX so tinha a mistura estereo, entao apagar uma regiao apagava tudo
que estivesse ali. Aqui os stems ja existem, e isso da uma informacao que
ele nao tinha: a **razao de posse**, ou seja, quanto de cada ponto do
espectro pertence a cada instrumento.

    posse_voz(t,f) = |Voz| / (|Voz| + |Baixo| + |Bateria| + ...)

Com isso, marcar uma regiao deixa de ser "apagar" e passa a ser "tirar a
parcela daquele instrumento", preservando o resto.

Limite que continua valendo: a fase e uma so por ponto. Dividir a
magnitude assume fases alinhadas, o que nunca e exato. Por isso atenuar
funciona bem e remover 100% sempre deixa rastro.

Memoria: nada e carregado por musica inteira. Tudo roda em blocos, o que
mantem o uso abaixo de ~150 MB mesmo com 8 GB de RAM no total.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from .util import log

JANELA = 4096          # 10.8 Hz por bin a 44.1 kHz
SALTO = 1024           # 23 ms entre quadros; 4x de sobreposicao
BLOCO_SEGUNDOS = 20    # quanto se processa de cada vez
PISO = 1e-10


# --------------------------------------------------------------------------- #
#  STFT / ISTFT
# --------------------------------------------------------------------------- #
def _janela(n: int) -> np.ndarray:
    """Raiz de Hann: usada na analise e na sintese, soma exata com salto n/4."""
    return np.sqrt(np.hanning(n + 1)[:n]).astype(np.float32)


def stft(x: np.ndarray, n: int = JANELA, salto: int = SALTO) -> np.ndarray:
    """(amostras,) -> (bins, quadros) complexo."""
    w = _janela(n)
    pad = n // 2
    xp = np.pad(x.astype(np.float32), (pad, pad + n), mode="constant")
    quadros = 1 + (len(xp) - n) // salto
    saida = np.empty((n // 2 + 1, quadros), dtype=np.complex64)
    for i in range(quadros):
        ini = i * salto
        saida[:, i] = np.fft.rfft(xp[ini:ini + n] * w)
    return saida


def istft(X: np.ndarray, tamanho: int, n: int = JANELA,
          salto: int = SALTO) -> np.ndarray:
    """Inverso com sobreposicao e soma, normalizado pela propria janela."""
    w = _janela(n)
    quadros = X.shape[1]
    total = (quadros - 1) * salto + n
    acc = np.zeros(total, dtype=np.float32)
    norma = np.zeros(total, dtype=np.float32)
    w2 = w * w
    for i in range(quadros):
        ini = i * salto
        acc[ini:ini + n] += np.fft.irfft(X[:, i], n).astype(np.float32) * w
        norma[ini:ini + n] += w2
    pad = n // 2
    y = acc[pad:pad + tamanho] / np.maximum(norma[pad:pad + tamanho], 1e-8)
    return y.astype(np.float32)


# --------------------------------------------------------------------------- #
#  Razao de posse
# --------------------------------------------------------------------------- #
def posse(mag_alvo: np.ndarray, mags_outros: list[np.ndarray],
          expoente: float = 2.0) -> np.ndarray:
    """Fracao de cada ponto do espectro que pertence ao stem alvo.

    Expoente 2 usa energia em vez de amplitude, o que separa melhor quando
    um instrumento domina o ponto (filtragem tipo Wiener).
    """
    a = np.power(np.maximum(mag_alvo, PISO), expoente)
    s = a.copy()
    for m in mags_outros:
        s += np.power(np.maximum(m, PISO), expoente)
    return (a / np.maximum(s, PISO)).astype(np.float32)


# --------------------------------------------------------------------------- #
#  Mascaras
# --------------------------------------------------------------------------- #
def _suavizar(m: np.ndarray, raio: int = 3) -> np.ndarray:
    """Borda abrupta gera artefato de sino. Suaviza nos dois eixos."""
    if raio < 1:
        return m
    k = np.hanning(2 * raio + 1).astype(np.float32)
    k /= k.sum()
    for eixo in (0, 1):
        m = np.apply_along_axis(
            lambda v: np.convolve(np.pad(v, raio, mode="edge"), k, mode="valid"),
            eixo, m).astype(np.float32)
    return np.clip(m, 0.0, 1.0)


def mascara_retangulo(forma: tuple[int, int], taxa: int,
                      t_ini: float, t_fim: float,
                      f_ini: float, f_fim: float,
                      desloc_s: float = 0.0,
                      salto: int = SALTO, n: int = JANELA) -> np.ndarray:
    """1 dentro do retangulo tempo x frequencia, 0 fora, com borda suave."""
    bins, quadros = forma
    m = np.zeros(forma, dtype=np.float32)

    q_ini = int(max(0, (t_ini - desloc_s) * taxa / salto))
    q_fim = int(min(quadros, (t_fim - desloc_s) * taxa / salto))
    hz_por_bin = (taxa / 2) / (bins - 1)
    b_ini = int(max(0, f_ini / hz_por_bin))
    b_fim = int(min(bins, f_fim / hz_por_bin + 1))

    if q_fim > q_ini and b_fim > b_ini:
        m[b_ini:b_fim, q_ini:q_fim] = 1.0
    return _suavizar(m)


def mascara_harmonicos(forma: tuple[int, int], taxa: int, f0: float,
                       n_harmonicos: int = 12, largura_hz: float = 25.0,
                       t_ini: float | None = None, t_fim: float | None = None,
                       desloc_s: float = 0.0,
                       salto: int = SALTO) -> np.ndarray:
    """Seleciona a fundamental e a serie harmonica dela.

    Tirar uma nota inteira soa natural; tirar uma faixa de frequencia abre
    um buraco na musica.
    """
    bins, quadros = forma
    m = np.zeros(forma, dtype=np.float32)
    hz_por_bin = (taxa / 2) / (bins - 1)

    q_ini = 0 if t_ini is None else int(max(0, (t_ini - desloc_s) * taxa / salto))
    q_fim = quadros if t_fim is None else int(min(quadros, (t_fim - desloc_s) * taxa / salto))
    if q_fim <= q_ini:
        return m

    for h in range(1, int(n_harmonicos) + 1):
        f = f0 * h
        if f >= taxa / 2:
            break
        # harmonico alto precisa de faixa proporcionalmente maior
        largura = largura_hz * (1 + 0.15 * (h - 1))
        b_ini = int(max(0, (f - largura) / hz_por_bin))
        b_fim = int(min(bins, (f + largura) / hz_por_bin + 1))
        if b_fim > b_ini:
            m[b_ini:b_fim, q_ini:q_fim] = 1.0
    return _suavizar(m, raio=2)


# --------------------------------------------------------------------------- #
#  Operacoes
# --------------------------------------------------------------------------- #
def aplicar(X_alvo: np.ndarray, mags_outros: list[np.ndarray],
            mascara: np.ndarray, operacao: str = "atenuar",
            ganho_db: float = -12.0, usar_posse: bool = True) -> np.ndarray:
    """Devolve o espectro do alvo com a operacao aplicada na regiao marcada.

    operacao:
      atenuar  - reduz a parcela do alvo dentro da regiao
      isolar   - mantem a regiao e zera o resto
      realcar  - aumenta a regiao
    """
    mag = np.abs(X_alvo)
    if usar_posse and mags_outros:
        p = posse(mag, mags_outros)
    else:
        p = np.ones_like(mag, dtype=np.float32)

    if operacao == "isolar":
        g = np.clip(mascara * p + (1 - mascara) * 0.0, 0.0, 1.0)
        return (X_alvo * g).astype(np.complex64)

    fator = float(10 ** (ganho_db / 20.0))
    # so muda o que a regiao marca E o que pertence ao alvo
    peso = np.clip(mascara * p, 0.0, 1.0)
    g = 1.0 + peso * (fator - 1.0)
    return (X_alvo * g).astype(np.complex64)


def limpar_vazamento(X_alvo: np.ndarray, mags_outros: list[np.ndarray],
                     forca: float = 1.0, piso_db: float = -18.0) -> np.ndarray:
    """Tira o vazamento dos outros instrumentos sem nenhuma selecao manual.

    Onde outro stem domina claramente o ponto do espectro, o alvo tem pouca
    posse ali: aquele pedaco e residuo, nao sinal. Atenua proporcionalmente.
    E o ganho mais alto do modulo, e nao exige desenhar nada.
    """
    mag = np.abs(X_alvo)
    p = posse(mag, mags_outros)
    piso = float(10 ** (piso_db / 20.0))
    g = piso + (1.0 - piso) * np.power(p, max(0.1, forca))
    g = _suavizar(g.astype(np.float32), raio=2)
    return (X_alvo * g).astype(np.complex64)


# --------------------------------------------------------------------------- #
#  Imagem do espectrograma
# --------------------------------------------------------------------------- #
def _mapa_cor(v: np.ndarray) -> np.ndarray:
    """0..1 -> RGB. Escuro no silencio, ambar no forte, igual ao player."""
    v = np.clip(v, 0, 1)
    r = np.clip(1.6 * v, 0, 1)
    g = np.clip(1.5 * v ** 1.6 - 0.10, 0, 1)
    b = np.clip(0.9 * v ** 3.2 + 0.06 * (1 - v), 0, 1)
    return (np.stack([r, g, b], axis=-1) * 255).astype(np.uint8)


def imagem_espectro(X: np.ndarray, taxa: int, altura: int = 480,
                    largura: int = 1100, f_max: float = 12000.0,
                    faixa_db: float = 80.0,
                    destaque: np.ndarray | None = None):
    """Espectrograma como imagem PIL, com escala de frequencia logaritmica.

    Log em frequencia porque e assim que o ouvido percebe: uma oitava ocupa
    o mesmo espaco em qualquer regiao.
    """
    from PIL import Image

    bins, quadros = X.shape
    mag = np.abs(X)
    db = 20 * np.log10(np.maximum(mag, PISO))
    db -= db.max()
    v = np.clip((db + faixa_db) / faixa_db, 0, 1)

    hz_por_bin = (taxa / 2) / (bins - 1)
    f_min = 40.0
    f_max = min(f_max, taxa / 2)
    # linhas da imagem: grave embaixo
    alvos = np.logspace(np.log10(f_min), np.log10(f_max), altura)
    idx_f = np.clip((alvos / hz_por_bin).astype(int), 0, bins - 1)
    idx_t = np.clip(np.linspace(0, quadros - 1, largura).astype(int), 0, quadros - 1)

    grade = v[np.ix_(idx_f, idx_t)][::-1]
    rgb = _mapa_cor(grade)

    if destaque is not None:
        d = destaque[np.ix_(idx_f, idx_t)][::-1]
        realce = (d[..., None] * np.array([70, 120, 190], dtype=np.float32))
        rgb = np.clip(rgb.astype(np.float32) + realce, 0, 255).astype(np.uint8)

    return Image.fromarray(rgb, mode="RGB")


# --------------------------------------------------------------------------- #
#  Leitura e processamento em blocos
# --------------------------------------------------------------------------- #
@dataclass
class Trecho:
    taxa: int
    inicio_s: float
    canais: list[np.ndarray]          # sinal por canal
    espectros: list[np.ndarray]       # STFT por canal


def ler_trecho(caminho: Path, inicio_s: float, duracao_s: float) -> Trecho:
    """Le so o pedaco pedido. Nunca carrega a musica inteira."""
    with sf.SoundFile(str(caminho)) as f:
        taxa = f.samplerate
        f.seek(min(int(inicio_s * taxa), max(0, len(f) - 1)))
        x = f.read(int(duracao_s * taxa), dtype="float32", always_2d=True)
    canais = [x[:, c] for c in range(x.shape[1])]
    return Trecho(taxa, inicio_s, canais, [stft(c) for c in canais])


def duracao(caminho: Path) -> float:
    with sf.SoundFile(str(caminho)) as f:
        return len(f) / f.samplerate


def processar_arquivo(alvo: Path, outros: list[Path], destino: Path,
                      operacao, progresso=None) -> Path:
    """Aplica 'operacao' na musica inteira, em blocos, com pouca memoria.

    operacao(X_alvo, mags_outros, taxa, inicio_s) -> X_alvo modificado
    """
    with sf.SoundFile(str(alvo)) as f:
        taxa, canais, total = f.samplerate, f.channels, len(f)

    bloco = int(BLOCO_SEGUNDOS * taxa)
    margem = JANELA * 4                      # evita costura audivel
    saida = np.zeros((total, canais), dtype=np.float32)

    pos = 0
    while pos < total:
        ini = max(0, pos - margem)
        fim = min(total, pos + bloco + margem)
        n_ler = fim - ini

        with sf.SoundFile(str(alvo)) as f:
            f.seek(ini)
            xa = f.read(n_ler, dtype="float32", always_2d=True)
        xo = []
        for o in outros:
            try:
                with sf.SoundFile(str(o)) as f:
                    f.seek(ini)
                    xo.append(f.read(n_ler, dtype="float32", always_2d=True))
            except Exception as e:
                log.warning("Ignorando %s: %s", o.name, e)

        for c in range(canais):
            Xa = stft(xa[:, c])
            mags = []
            for bruto in xo:
                cc = min(c, bruto.shape[1] - 1)
                m = np.abs(stft(bruto[:, cc]))
                if m.shape == Xa.shape:
                    mags.append(m)
            Xm = operacao(Xa, mags, taxa, ini / taxa)
            y = istft(Xm, n_ler)
            # descarta a margem, grava so o miolo
            a = pos - ini
            b = a + min(bloco, total - pos)
            saida[pos:pos + (b - a), c] = y[a:b]

        pos += bloco
        if progresso:
            try:
                progresso(min(1.0, pos / total), desc="Processando espectro")
            except Exception:
                pass

    destino.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destino), saida, taxa)
    return destino
