"""Estado da fila em JSON, para o lote sobreviver a queda, reboot ou OOM."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .util import RAIZ, log

ARQUIVO = RAIZ / "fila.json"


def carregar() -> dict:
    if ARQUIVO.exists():
        try:
            return json.loads(ARQUIVO.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("fila.json ilegivel, comecando do zero: %s", e)
    return {"itens": {}}


def salvar(estado: dict) -> None:
    tmp = ARQUIVO.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ARQUIVO)


def chave(caminho: Path, modo: str) -> str:
    return f"{str(caminho.resolve()).lower()}|{modo}"


def ja_feito(caminho: Path, modo: str) -> bool:
    item = carregar()["itens"].get(chave(caminho, modo))
    if not item or item.get("estado") != "ok":
        return False
    pasta = Path(item.get("pasta", ""))
    return pasta.exists() and any(pasta.glob("*"))


def marcar(caminho: Path, modo: str, estado: str,
           pasta: Path | None = None, erro: str = "") -> None:
    d = carregar()
    d["itens"][chave(caminho, modo)] = {
        "arquivo": str(caminho),
        "modo": modo,
        "estado": estado,
        "pasta": str(pasta) if pasta else "",
        "erro": erro[:500],
        "quando": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    salvar(d)


def limpar() -> None:
    salvar({"itens": {}})
