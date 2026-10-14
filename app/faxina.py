"""Faxina das pastas de saida.

Duas coisas acumulam lixo:

1. Faixas duplicadas de um bug antigo. Ao separar a mesma musica duas vezes,
   a regra de conflito disparava contra os arquivos da rodada anterior e
   gravava tudo de novo com o nome da musica na frente. O mixer somava as
   duas copias e o som ficava metalico.

2. Mixagens derivadas em WAV. Cada uma pesa o dobro de um FLAC e so serve
   para ouvir uma vez.

Nada e apagado sem voce confirmar.
"""
from __future__ import annotations

from pathlib import Path

from .festa import AUDIO_EXT, ORDEM, _e_derivado, _e_fonte
from .util import PASTA_SAIDA


def _duplicadas(pasta: Path) -> list[Path]:
    """Arquivos <nome da musica>_<faixa> que repetem uma faixa ja existente."""
    prefixo = pasta.name.lower() + "_"
    presentes = {f.stem.lower() for f in pasta.iterdir()
                 if f.is_file() and f.suffix.lower() in AUDIO_EXT}
    achados = []
    for f in sorted(pasta.iterdir()):
        if not f.is_file() or f.suffix.lower() not in AUDIO_EXT:
            continue
        haste = f.stem.lower()
        if not haste.startswith(prefixo):
            continue
        original = haste[len(prefixo):]
        # so e duplicata se a versao sem prefixo existir de verdade
        if original in presentes and original in ORDEM:
            achados.append(f)
    return achados


def _derivadas(pasta: Path) -> list[Path]:
    return [f for f in sorted(pasta.iterdir())
            if f.is_file() and f.suffix.lower() in AUDIO_EXT
            and _e_derivado(f.stem)]


def levantar() -> dict:
    dupes, deriv = {}, {}
    if not PASTA_SAIDA.exists():
        return {"duplicadas": dupes, "derivadas": deriv}
    for pasta in sorted(PASTA_SAIDA.iterdir()):
        if not pasta.is_dir():
            continue
        d = _duplicadas(pasta)
        v = _derivadas(pasta)
        if d:
            dupes[pasta.name] = d
        if v:
            deriv[pasta.name] = v
    return {"duplicadas": dupes, "derivadas": deriv}


def _mb(arquivos) -> float:
    return sum(f.stat().st_size for f in arquivos) / 1e6


def main() -> None:
    achados = levantar()
    dupes, deriv = achados["duplicadas"], achados["derivadas"]

    print("=" * 68)
    print(" KaraokeSep - faxina")
    print("=" * 68)

    if not dupes and not deriv:
        print("\nNada para limpar. Suas pastas estao em ordem.\n")
        return

    if dupes:
        total = sum(_mb(v) for v in dupes.values())
        print(f"\nFAIXAS DUPLICADAS  ({total:.0f} MB)")
        print("Sao copias da mesma faixa. O mixer soma as duas e o som fica")
        print("metalico. Apagar e seguro: a versao boa fica.\n")
        for nome, arquivos in dupes.items():
            print(f"  [{nome}]")
            for f in arquivos:
                print(f"     {f.name}  ({f.stat().st_size/1e6:.1f} MB)")

    if deriv:
        total = sum(_mb(v) for v in deriv.values())
        print(f"\nMIXAGENS DERIVADAS  ({total:.0f} MB)")
        print("Bases e testes gerados a partir das faixas. Nao entram no")
        print("mixer e podem ser refeitos a qualquer momento.\n")
        for nome, arquivos in deriv.items():
            print(f"  [{nome}]")
            for f in arquivos:
                print(f"     {f.name}  ({f.stat().st_size/1e6:.1f} MB)")

    print("\n" + "-" * 68)
    print("  1  apagar so as faixas duplicadas  (recomendado)")
    print("  2  apagar so as mixagens derivadas")
    print("  3  apagar as duas coisas")
    print("  0  sair sem apagar nada")
    escolha = input("\nEscolha: ").strip()

    alvos = []
    if escolha in ("1", "3"):
        alvos += [f for v in dupes.values() for f in v]
    if escolha in ("2", "3"):
        alvos += [f for v in deriv.values() for f in v]
    if not alvos:
        print("\nNada apagado.\n")
        return

    apagados, falhas = 0, 0
    liberado = 0.0
    for f in alvos:
        try:
            liberado += f.stat().st_size / 1e6
            f.unlink()
            apagados += 1
        except Exception as e:
            print(f"  nao consegui apagar {f.name}: {e}")
            falhas += 1
    print(f"\n{apagados} arquivo(s) apagados, {liberado:.0f} MB liberados"
          + (f", {falhas} falha(s)" if falhas else "") + ".")
    print("Rode o festa.bat e confira se a musica soa igual ao original.\n")


if __name__ == "__main__":
    main()
