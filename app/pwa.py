"""Arquivos para instalar o modo festa como aplicativo (PWA).

O navegador so oferece "Instalar" quando a pagina tem manifesto, icones e um
service worker. Em http://127.0.0.1 isso vale como contexto seguro, entao
funciona no Chrome e no Edge do PC sem certificado nenhum.

O app instalado abre numa janela propria, sem barra de enderecos, mas continua
precisando do servidor local: o festa.bat tem que estar aberto.

O service worker NAO guarda nada em cache. A pagina e os audios mudam o tempo
todo (a pagina e regerada a cada abertura) e um cache velho serviria a versao
errada depois de uma atualizacao.
"""
from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import numpy as np

COR_FUNDO = (11, 13, 16)
COR_NOTA = (255, 138, 43)
COR_ONDA = (40, 209, 196)

MANIFESTO = {
    "name": "KaraokeSep - Modo festa",
    "short_name": "Festa",
    "description": "Karaoke com mixer por instrumento, letra e partitura.",
    "start_url": "/festa.html",
    "scope": "/",
    "display": "standalone",
    "background_color": "#0b0d10",
    "theme_color": "#0b0d10",
    "lang": "pt-BR",
    "icons": [
        {"src": "/icone-192.png", "sizes": "192x192", "type": "image/png",
         "purpose": "any"},
        {"src": "/icone-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any"},
        {"src": "/icone-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ],
}

# Sem respondWith: o navegador segue o caminho normal da rede (inclusive Range
# nos audios). O ouvinte existe porque o Chrome o exige para oferecer a
# instalacao.
SERVICE_WORKER = """/* Modo festa: service worker sem cache, so para habilitar a instalacao. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
"""


def _png(rgb: np.ndarray) -> bytes:
    """RGB uint8 (h, w, 3) -> bytes de um PNG."""
    h, w, _ = rgb.shape
    cru = np.concatenate([np.zeros((h, 1), np.uint8), rgb.reshape(h, w * 3)], axis=1)

    def bloco(tipo: bytes, dados: bytes) -> bytes:
        corpo = tipo + dados
        return (struct.pack(">I", len(dados)) + corpo
                + struct.pack(">I", zlib.crc32(corpo) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + bloco(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + bloco(b"IDAT", zlib.compress(cru.tobytes(), 9))
            + bloco(b"IEND", b""))


def _desenhar(tam: int) -> np.ndarray:
    """Colcheia laranja com duas ondas, em fundo escuro. Conteudo dentro dos
    80% centrais, para sobreviver ao recorte de icone "maskable"."""
    sub = 3                                    # subamostras por eixo (bordas suaves)
    n = tam * sub
    ys, xs = np.mgrid[0:n, 0:n]
    x = (xs + 0.5) / n
    y = (ys + 0.5) / n

    def segmento(x0, y0, x1, y1, larg):
        """pontos a menos de larg/2 do segmento (x0,y0)-(x1,y1)"""
        vx, vy = x1 - x0, y1 - y0
        t = np.clip(((x - x0) * vx + (y - y0) * vy) / (vx * vx + vy * vy), 0, 1)
        return np.hypot(x - (x0 + t * vx), y - (y0 + t * vy)) <= larg / 2

    # cabeca da nota: elipse inclinada
    cx, cy, rx, ry, ang = 0.34, 0.66, 0.125, 0.09, np.radians(-22)
    dx, dy = x - cx, y - cy
    u = dx * np.cos(ang) + dy * np.sin(ang)
    v = -dx * np.sin(ang) + dy * np.cos(ang)
    cabeca = (u / rx) ** 2 + (v / ry) ** 2 <= 1

    # haste e bandeirola (traco curvo em dois segmentos)
    hx = cx + rx * 0.82
    haste = segmento(hx, 0.26, hx, cy, 0.045)
    bandeira = (segmento(hx, 0.26, hx + 0.12, 0.36, 0.05)
                | segmento(hx + 0.12, 0.36, hx + 0.13, 0.47, 0.05))

    # ondas de som a direita
    ox, oy = 0.60, 0.50
    r = np.hypot(x - ox, y - oy)
    ondas = (((np.abs(r - 0.13) <= 0.018) | (np.abs(r - 0.24) <= 0.018))
             & (np.abs(np.arctan2(y - oy, x - ox)) <= 0.85))

    img = np.empty((n, n, 3), np.float32)
    img[:] = COR_FUNDO
    for mascara, cor in ((ondas, COR_ONDA), (cabeca | haste | bandeira, COR_NOTA)):
        img[mascara] = cor
    img = img.reshape(tam, sub, tam, sub, 3).mean(axis=(1, 3))
    return np.clip(img + 0.5, 0, 255).astype(np.uint8)


def escrever(pasta: Path) -> None:
    """Grava manifesto, service worker e icones ao lado do festa.html."""
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "manifest.webmanifest").write_text(
        json.dumps(MANIFESTO, ensure_ascii=False, indent=1), encoding="utf-8")
    (pasta / "sw.js").write_text(SERVICE_WORKER, encoding="utf-8")
    for tam in (192, 512):
        destino = pasta / f"icone-{tam}.png"
        if not destino.exists():
            destino.write_bytes(_png(_desenhar(tam)))
