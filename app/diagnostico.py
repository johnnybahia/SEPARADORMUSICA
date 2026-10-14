"""Diagnostico das pastas de saida.

Responde a pergunta "por que essa faixa nao aparece no mixer?" olhando o
disco e a regra, em vez de adivinhar. Rode diagnostico.bat.
"""
from __future__ import annotations

from pathlib import Path

from .festa import (AUDIO_EXT, DERIVADOS, ORDEM, PAIS, _e_derivado, _e_fonte,
                    varrer)
from .util import PASTA_SAIDA


def _motivo(haste: str, pasta_nome: str, presentes: set[str]) -> str:
    if _e_derivado(haste):
        return "mixagem derivada, nao e faixa"
    if _e_fonte(haste, pasta_nome):
        return "e o arquivo original da musica"
    for pai, filhos in PAIS.items():
        if haste == pai and (filhos & presentes):
            juntos = ", ".join(sorted(filhos & presentes))
            return f"e a soma de {juntos}; somar os dois duplicaria o audio"
    return "?"


def relatorio() -> str:
    linhas = ["=" * 68,
              " KaraokeSep - diagnostico das pastas",
              "=" * 68, ""]
    if not PASTA_SAIDA.exists():
        return "\n".join(linhas + ["Pasta 'saida' nao existe."])

    fila = {m["titulo"]: m for m in varrer()}
    pastas = [p for p in sorted(PASTA_SAIDA.iterdir()) if p.is_dir()]
    if not pastas:
        return "\n".join(linhas + ["Nenhuma musica em 'saida'."])

    for pasta in pastas:
        arquivos = [f for f in sorted(pasta.iterdir())
                    if f.is_file() and f.suffix.lower() in AUDIO_EXT]
        linhas.append(f"[{pasta.name}]")
        if not arquivos:
            linhas += ["   sem audio nenhum", ""]
            continue

        item = fila.get(pasta.name)
        usados = {c["chave"] for c in item["canais"]} if item else set()
        presentes = {f.stem.lower() for f in arquivos
                     if not _e_derivado(f.stem)
                     and not _e_fonte(f.stem, pasta.name)}

        for f in arquivos:
            haste = f.stem.lower()
            mb = f.stat().st_size / 1e6
            if haste in usados:
                marca = "  toca "
                motivo = "" if haste in ORDEM else "   <- nome novo, entrou assim mesmo"
            else:
                marca = "  FORA "
                motivo = "   <- " + _motivo(haste, pasta.name, presentes)
            vazio = "  (ARQUIVO VAZIO)" if f.stat().st_size < 2048 else ""
            linhas.append(f"{marca} {f.name:<34} {mb:7.1f} MB{vazio}{motivo}")

        linhas.append(f"   canais no mixer: {len(usados)} de {len(arquivos)} arquivos")
        if item and not item["temFaixas"]:
            linhas.append("   modo: cancelamento ao vivo (sem faixas separadas)")
        if item:
            linhas.append(f"   letra: {len(item['letra'])} linhas")
        linhas.append("")

    linhas += ["-" * 68,
               "O mixer aceita QUALQUER faixa. Ficam de fora so:",
               "  - mixagens derivadas: " + ", ".join(DERIVADOS),
               "  - o arquivo original da musica",
               "  - o stem pai quando as partes dele estao na pasta",
               "",
               "Nomes conhecidos, que aparecem em ordem no mixer:",
               "  " + ", ".join(ORDEM),
               "-" * 68]
    return "\n".join(linhas)


if __name__ == "__main__":
    print(relatorio())
