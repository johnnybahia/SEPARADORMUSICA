"""Modo festa.

Pagina HTML unica, servida por um servidor local, que toca a fila inteira
com processamento ao vivo no navegador e TAMBEM comanda a separacao das
faixas. Assim o dia a dia nao precisa do programa com abas.

Dois motores de reproducao, escolhidos por musica:

  COM FAIXAS  - ganho independente por instrumento. O botao de voz muda o
                nivel do stem vocal. Qualidade maxima, custo zero de CPU.

  SEM FAIXAS  - cancelamento de centro ao vivo, aplicado SO na banda da voz
                (150 Hz a 8 kHz). Grave e agudo passam em estereo intacto,
                que e o que separa isso do L-R cru: aquele leva o bumbo e o
                baixo junto e deixa a musica oca.

Limite honesto do segundo motor: voz com alargamento estereo, dobras ou
reverb nos lados nao some. Em gravacao com voz seca no centro funciona bem.
Nao ha meio-termo, e da para ouvir na hora.

O servidor e obrigatorio, nao capricho: o navegador trata arquivo aberto por
file:// como origem isolada e o Web Audio devolve silencio, sem erro.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import queue
import re
import shutil
import socketserver
import threading
import time
import urllib.parse
from pathlib import Path

from .util import PASTA_SAIDA, log, nome_seguro, nome_unico

AUDIO_EXT = [".mp3", ".flac", ".wav", ".ogg", ".m4a", ".opus", ".aac",
             ".wma", ".aiff", ".aif"]
ORDEM = ["vocal", "vocal_principal", "voz_masculina", "voz_feminina", "coro",
         "vocal_outros", "instrumental", "bateria",
         "bumbo", "caixa", "toms", "chimbal", "pratos", "conducao", "crash",
         "bateria_outros", "baixo", "guitarra", "piano", "outros"]
# O botao de voz age sobre estas faixas. Sem as vozes divididas na lista, um
# dueto separado em masculina e feminina ignorava o botao por completo.
VOCAIS = {"vocal", "vocal_principal", "voz_masculina", "voz_feminina", "coro"}
DERIVADOS = ("base_", "treino_", "referencia_", "_editado", "_limpo",
             "_forte", "_parelho", "_ganho", "_transposto", "51_")

# Um stem e a soma dos filhos dele. Se os dois estiverem na pasta e o mixer
# somar tudo, aquele audio entra DUAS vezes: o ganho dobra e as pequenas
# diferencas de fase entre a versao inteira e a soma das partes viram
# filtro pente, que e o som metalico de lata. O pai sai quando qualquer
# filho aparece.
PECAS = {"bumbo", "caixa", "toms", "pratos", "chimbal", "conducao", "crash",
         "bateria_outros"}
PAIS = {
    "instrumental": {"bateria", "baixo", "guitarra", "piano", "outros"} | PECAS,
    "bateria": PECAS,
    "vocal": {"vocal_principal", "coro", "voz_masculina", "voz_feminina",
              "vocal_outros"},
    "vocal_principal": {"voz_masculina", "voz_feminina"},
}

# rotulos e estimativas mostrados na pagina
MODOS = [
    {"id": "karaoke",  "n": 1, "rotulo": "Karaoke",  "faixas": "2 faixas"},
    {"id": "padrao",   "n": 2, "rotulo": "Padrao",   "faixas": "4 faixas"},
    {"id": "padrao_6", "n": 3, "rotulo": "Seis",     "faixas": "6 faixas"},
    {"id": "profundo", "n": 4, "rotulo": "Profundo", "faixas": "10 faixas"},
]


def _e_derivado(haste: str) -> bool:
    h = haste.lower()
    return any(h.startswith(x) or h.endswith(x) for x in DERIVADOS)


def _modos_com_tempo() -> list[dict]:
    """Estimativa por modo, usando o que ja foi medido nesta maquina.

    As passadas da cascata usam modelos de outra familia (Roformer e
    MDX23C) e podem custar dez vezes o htdemucs na mesma CPU. Enquanto
    nao houver medicao, o numero vem marcado como estimativa.
    """
    from . import hardware
    from .separador import tempos_medidos

    perfil = hardware.detectar()
    medidos = tempos_medidos()
    forte = perfil.gpu or perfil.threads >= 8

    # quais passadas cada modo dispara
    RECEITA = {
        "karaoke":  [("voz_qualidade" if forte else "voz_rapido", 1)],
        "padrao":   [("stems_qualidade" if perfil.gpu
                      else ("stems4" if forte else "stems4_rapido"), 1)],
        "padrao_6": [("stems_qualidade" if forte else "stems6", 1)],
        "profundo": [("stems_qualidade" if forte else "stems6", 1),
                     ("drumsep", 1), ("backing", 1)],
    }
    # chute inicial em segundos por minuto de audio, so ate medir
    CHUTE = {"voz_rapido": 110, "voz_qualidade": 300, "stems4_rapido": 73,
             "stems4": 290, "stems_qualidade": 300, "stems6": 100,
             "drumsep": 330, "backing": 330}
    if perfil.gpu:
        CHUTE = {k: max(4, v // 12) for k, v in CHUTE.items()}

    saida = []
    for m in MODOS:
        seg, medido_tudo = 0.0, True
        for papel, vezes in RECEITA[m["id"]]:
            if papel in medidos:
                seg += medidos[papel]["seg_por_min"] * 4 * vezes
            else:
                seg += CHUTE.get(papel, 120) * 4 * vezes
                medido_tudo = False
        rotulo = ("~%d min" % round(seg / 60) if seg >= 90 else "~%d s" % seg)
        if not medido_tudo:
            rotulo += "?"
        saida.append(dict(m, tempo=rotulo, medido=medido_tudo,
                          recomendado=(m["id"] == "padrao")))
    return saida


# --------------------------------------------------------------------------- #
#  Varredura da pasta de saida
# --------------------------------------------------------------------------- #
def _e_fonte(haste: str, pasta_nome: str) -> bool:
    """Reconhece o arquivo original da musica, que nao e uma faixa."""
    h = haste.lower().strip()
    if h in ("entrada", "entrada_multicanal", "original"):
        return True
    return h == pasta_nome.lower().strip()


# --------------------------------------------------------------------------- #
#  Partituras
#
#  Geradas no Colab, dentro de saida/<musica>/partituras/. O player so le:
#    partitura.json        batidas, compasso, tonalidade, confianca por compasso
#    <faixa>.svg           a partitura numa pagina longa, para rolar na tela
#    <faixa>.timemap.json  quais notas soam em cada batida (ids do SVG)
#    <faixa>.pdf/.musicxml/.mid   para baixar
# --------------------------------------------------------------------------- #
FORMATO_PARTITURA = 2
TIPOS_PARTITURA = ("pdf", "musicxml", "mid")


def _ler_partitura(pasta: Path) -> dict | None:
    jp = pasta / "partituras" / "partitura.json"
    if not jp.is_file():
        return None
    try:
        d = json.loads(jp.read_text(encoding="utf-8"))
    except Exception as e:
        log.debug("partitura.json ilegivel em %s: %s", pasta.name, e)
        return None
    if not isinstance(d, dict) or int(d.get("versao", 0)) != FORMATO_PARTITURA:
        return None
    return d


def _arquivos_existentes(pasta: Path, arquivos: dict) -> dict:
    """So aceita nome simples (sem barra) e que exista de fato."""
    base = pasta / "partituras"
    return {k: v for k, v in (arquivos or {}).items()
            if isinstance(v, str) and v == Path(v).name and (base / v).is_file()}


def _resumo_partitura(pasta: Path) -> dict | None:
    """O que a fila precisa saber. As listas grandes (batidas, confianca por
    compasso) ficam para /api/partitura, so quando a aba abre."""
    d = _ler_partitura(pasta)
    if not d:
        return None
    stems = {}
    for chave, s in (d.get("stems") or {}).items():
        arqs = _arquivos_existentes(pasta, s.get("arquivos"))
        if not arqs:
            continue
        stems[chave] = {
            "nome": s.get("nome") or chave, "tonalidade": s.get("tonalidade", ""),
            "notas": s.get("notas", 0), "confianca": s.get("confianca", 0),
            "aviso": s.get("aviso", ""), "hash": s.get("hash", ""),
            "tela": "svg" in arqs and "timemap" in arqs,
            "arquivos": arqs,
        }
    if not stems:
        return None
    return {"bpm": d.get("bpm"), "compasso": d.get("compasso", [4, 4]),
            "fonte_batidas": d.get("fonte_batidas", ""), "stems": stems}


def partitura_completa(titulo: str) -> dict | None:
    """partitura.json inteiro, para o titulo de uma musica que esta na fila."""
    pasta = next((PASTA_SAIDA / m["titulo"] for m in varrer()
                  if m["titulo"] == titulo), None)
    return _ler_partitura(pasta) if pasta else None


def zip_partituras(titulo: str, tipo: str = "pdf") -> bytes | None:
    """Zip com a partitura de cada faixa no formato pedido."""
    import io
    import zipfile
    if tipo not in TIPOS_PARTITURA:
        return None
    pasta = next((PASTA_SAIDA / m["titulo"] for m in varrer()
                  if m["titulo"] == titulo), None)
    d = _ler_partitura(pasta) if pasta else None
    if not d:
        return None
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for chave, s in (d.get("stems") or {}).items():
            nome = _arquivos_existentes(pasta, s.get("arquivos")).get(tipo)
            if nome:
                z.write(pasta / "partituras" / nome,
                        f"{titulo} - {s.get('nome') or chave}.{tipo}")
                n += 1
    return buf.getvalue() if n else None


def varrer() -> list[dict]:
    if not PASTA_SAIDA.exists():
        return []
    fila = []
    for pasta in sorted(PASTA_SAIDA.iterdir()):
        if not pasta.is_dir():
            continue

        # Qualquer audio que nao seja mixagem derivada nem o arquivo original
        # conta como faixa. Antes so entravam os nomes de uma lista fechada,
        # e qualquer stem batizado de um jeito imprevisto ficava invisivel no
        # mixer, sem erro nenhum: a musica simplesmente tocava incompleta.
        faixas, fontes = {}, []
        for f in sorted(pasta.iterdir()):
            if not f.is_file() or f.suffix.lower() not in AUDIO_EXT:
                continue
            haste = f.stem.lower()
            if _e_derivado(haste):
                continue
            if _e_fonte(haste, pasta.name):
                fontes.append(f)
            else:
                faixas[haste] = f

        # uma pasta com um audio so nao foi separada: ele e a musica inteira
        if len(faixas) == 1 and not fontes:
            unica = next(iter(faixas))
            if unica not in ORDEM:
                fontes = [faixas.pop(unica)]

        if not faixas and not fontes:
            continue

        # tira o pai quando a decomposicao dele esta presente
        presentes = set(faixas)
        for pai, filhos in PAIS.items():
            if pai in faixas and (filhos & presentes):
                faixas.pop(pai, None)

        item = {"titulo": pasta.name, "temFaixas": bool(faixas),
                "canais": [], "unico": "", "letra": [],
                "partitura": _resumo_partitura(pasta)}
        if faixas:
            def ordenar(k):
                return (ORDEM.index(k) if k in ORDEM else 99, k)
            for nome in sorted(faixas, key=ordenar):
                item["canais"].append({
                    "nome": nome.replace("_", " "), "chave": nome,
                    "arquivo": f"{pasta.name}/{faixas[nome].name}",
                    "voz": nome in VOCAIS})
        elif fontes:
            item["unico"] = f"{pasta.name}/{fontes[0].name}"

        lrc = pasta / "letra.lrc"
        if lrc.exists():
            try:
                from .letras import analisar_lrc
                item["letra"] = analisar_lrc(
                    lrc.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                log.debug("LRC ilegivel em %s: %s", pasta.name, e)
        fila.append(item)
    return fila


# --------------------------------------------------------------------------- #
#  Trabalhador de separacao
#
#  Uma musica de cada vez, numa thread propria. A pagina consulta o estado
#  e mostra a barra. Separar enquanto a musica toca briga por CPU: com dois
#  nucleos o audio falha, e a pagina avisa isso.
# --------------------------------------------------------------------------- #
_tarefas: "queue.Queue[dict]" = queue.Queue()
_trabalhador = None
_estado = {"atual": None, "espera": [], "feitos": [], "erros": []}
_trava = threading.Lock()


def _fonte_da_pasta(pasta: Path) -> Path | None:
    """O arquivo original de uma pasta que ainda nao foi separada."""
    for f in sorted(pasta.iterdir()):
        if (f.is_file() and f.suffix.lower() in AUDIO_EXT
                and f.stem.lower() not in ORDEM and not _e_derivado(f.stem)):
            return f
    return None


def _separar_uma(titulo: str, modo: str) -> None:
    from . import canais, letras, treino
    from .hardware import detectar, impedir_suspensao, liberar_suspensao
    from .separador import Motor

    pasta = PASTA_SAIDA / titulo
    fonte = _fonte_da_pasta(pasta)
    if fonte is None:
        raise RuntimeError("nao achei o arquivo original nessa pasta")

    def prog(frac, desc=""):
        with _trava:
            if _estado["atual"]:
                _estado["atual"]["frac"] = float(frac)
                _estado["atual"]["texto"] = desc

    impedir_suspensao()
    try:
        prog(0.02, "Preparando o audio")
        entrada, _ = canais.preparar_entrada(fonte)

        motor = Motor(detectar(), formato="FLAC")
        res = motor.processar(entrada, pasta, modo, progresso=prog)

        prog(0.94, "Montando o instrumental")
        inst = treino.instrumental(res.stems, pasta / "base_instrumental.wav")
        if inst:
            try:
                treino.converter(inst, pasta / "instrumental.flac", "FLAC")
                inst.unlink(missing_ok=True)
            except Exception as e:
                log.warning("Nao converti o instrumental: %s", e)

        if not (pasta / "letra.lrc").exists():
            prog(0.97, "Buscando a letra")
            try:
                titulo_m, artista, album = letras.ler_tags(fonte)
                achado = letras.buscar(titulo_m, artista, album)
                if achado and achado.tem_sincronia:
                    letras.salvar_lrc(achado, pasta / "letra.lrc")
            except Exception as e:
                log.debug("Letra nao encontrada: %s", e)
        prog(1.0, "Pronto")
    finally:
        liberar_suspensao()


def separar_vozes(titulo: str) -> dict:
    """Divide o stem de voz em masculina e feminina.

    Passada extra sobre vocal.flac, nao substitui a separacao principal.
    O modelo tem SDR 7.29, bem abaixo dos stems instrumentais: espere
    vazamento audivel, principalmente onde os dois cantam ao mesmo tempo.
    Duas vozes de timbre parecido nao separam de jeito nenhum.
    """
    from .hardware import detectar, impedir_suspensao, liberar_suspensao
    from .separador import Motor

    pasta = PASTA_SAIDA / titulo
    if not pasta.is_dir():
        raise ValueError("pasta nao encontrada")

    vocal = None
    for cand in ("vocal", "vocal_principal"):
        for ext in AUDIO_EXT:
            f = pasta / (cand + ext)
            if f.exists():
                vocal = f
                break
        if vocal:
            break
    if vocal is None:
        raise ValueError("essa musica ainda nao tem faixa de voz; "
                         "separe as faixas primeiro")

    def prog(frac, desc=""):
        with _trava:
            if _estado["atual"]:
                _estado["atual"]["frac"] = float(frac)
                _estado["atual"]["texto"] = desc

    impedir_suspensao()
    try:
        prog(0.05, "Dividindo as vozes")
        motor = Motor(detectar(), formato="FLAC")
        novos = motor.passada(vocal, "vozes", pasta)
        prog(1.0, "Pronto")
        return {"ok": True, "faixas": sorted(novos)}
    finally:
        liberar_suspensao()


def _laco():
    while True:
        tarefa = _tarefas.get()
        tipo = tarefa.get("tipo", "separar")
        with _trava:
            _estado["atual"] = {"titulo": tarefa["titulo"], "modo": tarefa["modo"],
                                "frac": 0.0, "texto": "Comecando",
                                "inicio": time.time()}
            _estado["espera"] = [t for t in _estado["espera"]
                                 if t != tarefa["titulo"]]
        try:
            if tipo == "vozes":
                separar_vozes(tarefa["titulo"])
            else:
                _separar_uma(tarefa["titulo"], tarefa["modo"])
            with _trava:
                _estado["feitos"].append(tarefa["titulo"])
            log.info("Concluido (%s): %s", tipo, tarefa["titulo"])
        except Exception as e:
            log.error("Falha em %s (%s): %s", tarefa["titulo"], tipo, e,
                      exc_info=True)
            with _trava:
                _estado["erros"].append({"titulo": tarefa["titulo"],
                                         "erro": str(e)[:300]})
        finally:
            with _trava:
                _estado["atual"] = None
            _tarefas.task_done()


def _ocupado() -> bool:
    """Ha separacao rodando ou na fila? A atualizacao reinicia o servidor."""
    with _trava:
        return bool(_estado["atual"] or _estado["espera"])


def _garantir_trabalhador():
    global _trabalhador
    if _trabalhador is None or not _trabalhador.is_alive():
        _trabalhador = threading.Thread(target=_laco, daemon=True)
        _trabalhador.start()


def enfileirar(titulo: str, modo: str, tipo: str = "separar") -> dict:
    if tipo == "separar" and modo not in {m["id"] for m in MODOS}:
        raise ValueError("modo desconhecido")
    pasta = PASTA_SAIDA / titulo
    if not pasta.is_dir():
        raise ValueError("pasta nao encontrada")
    with _trava:
        ja = ((_estado["atual"] or {}).get("titulo") == titulo
              or titulo in _estado["espera"])
        if ja:
            return {"ok": True, "aviso": "ja esta na fila"}
        _estado["espera"].append(titulo)
    _garantir_trabalhador()
    _tarefas.put({"titulo": titulo, "modo": modo, "tipo": tipo})
    return {"ok": True}


def enfileirar_todas(modo: str) -> dict:
    """Poe na fila toda musica que ainda nao tem faixas separadas.

    Uma de cada vez, em sequencia. Rodar duas em paralelo com dois nucleos
    seria mais lento que rodar em fila: elas brigariam pelo mesmo processador
    e ainda dobrariam o uso de memoria.
    """
    pendentes = [m["titulo"] for m in varrer() if not m["temFaixas"]]
    postas = []
    for titulo in pendentes:
        try:
            r = enfileirar(titulo, modo)
            if not r.get("aviso"):
                postas.append(titulo)
        except Exception as e:
            log.warning("Nao enfileirei %s: %s", titulo, e)
    return {"ok": True, "postas": len(postas), "pendentes": len(pendentes)}


def cancelar(tudo: bool = True) -> dict:
    """Esvazia a fila de espera.

    A musica que ja esta rodando nao para no meio: a chamada de separacao e
    bloqueante e interromper deixaria arquivo pela metade. Ela termina e
    depois nada mais comeca.
    """
    removidas = 0
    while True:
        try:
            _tarefas.get_nowait()
            _tarefas.task_done()
            removidas += 1
        except queue.Empty:
            break
    with _trava:
        _estado["espera"] = []
        atual = (_estado["atual"] or {}).get("titulo")
    return {"ok": True, "removidas": removidas, "terminando": atual}


def assinatura() -> str:
    """Impressao digital barata da pasta saida.

    Muda quando uma pasta e criada, apagada ou ganha arquivos. Serve para a
    pagina perceber musicas que chegaram por fora — descompactadas de um zip
    do Colab, por exemplo — sem precisar varrer tudo a cada segundo.
    """
    try:
        partes = []
        for d in sorted(PASTA_SAIDA.iterdir()):
            if not d.is_dir():
                continue
            st = d.stat()
            partes.append(f"{d.name}:{int(st.st_mtime)}:{len(list(d.iterdir()))}")
        return "|".join(partes)
    except Exception:
        return ""


def estado() -> dict:
    with _trava:
        atual = dict(_estado["atual"]) if _estado["atual"] else None
        if atual:
            atual["decorrido"] = int(time.time() - atual.pop("inicio", time.time()))
        return {"atual": atual, "espera": list(_estado["espera"]),
                "feitos": list(_estado["feitos"]),
                "erros": list(_estado["erros"]),
                "assinatura": assinatura()}


def _audio_da_pasta(pasta: Path) -> Path | None:
    """Qualquer audio util da pasta, para medir a duracao."""
    for f in sorted(pasta.iterdir()):
        if f.is_file() and f.suffix.lower() in AUDIO_EXT and not _e_derivado(f.stem):
            return f
    return None


def _duracao(caminho: Path) -> float | None:
    try:
        import soundfile as sf
        with sf.SoundFile(str(caminho)) as f:
            return len(f) / f.samplerate
    except Exception:
        return None


def buscar_letra(titulo: str, artista: str = "", nome_musica: str = "") -> dict:
    """Procura a letra sincronizada no LRCLIB e grava letra.lrc na pasta.

    Os campos de artista e musica podem vir da pagina: nome de pasta vindo
    de site de download quase nunca casa, e deixar o usuario corrigir e o
    que faz a busca funcionar.
    """
    from . import letras

    pasta = PASTA_SAIDA / titulo
    if not pasta.is_dir():
        raise ValueError("pasta nao encontrada")

    audio = _audio_da_pasta(pasta)
    dur = _duracao(audio) if audio else None

    if not (artista or nome_musica):
        base = audio if audio else pasta
        t, a, _ = letras.ler_tags(base if audio else pasta / "x.mp3")
        nome_musica, artista = t, a
    nome_musica = letras.limpar_titulo(nome_musica or titulo)
    artista = letras.limpar_titulo(artista)

    achado = letras.buscar(nome_musica, artista, "", dur)
    if achado and achado.tem_sincronia:
        letras.salvar_lrc(achado, pasta / "letra.lrc")
        n = len([l for l in achado.sincronizada.splitlines() if l.strip()])
        return {"ok": True, "fonte": achado.fonte, "linhas": n,
                "titulo": achado.titulo, "artista": achado.artista}
    if achado:
        (pasta / "letra_sem_tempo.txt").write_text(achado.simples or "",
                                                   encoding="utf-8")
        return {"ok": False,
                "motivo": "achei a letra mas sem marcacao de tempo; "
                          "gravei em letra_sem_tempo.txt"}
    return {"ok": False,
            "motivo": "nada encontrado. Confira o nome do artista e da musica, "
                      "ou cole o LRC na caixa abaixo"}


def salvar_letra(titulo: str, texto: str) -> dict:
    """Grava um LRC colado a mao."""
    from .letras import analisar_lrc

    pasta = PASTA_SAIDA / titulo
    if not pasta.is_dir():
        raise ValueError("pasta nao encontrada")
    linhas = analisar_lrc(texto or "")
    if not linhas:
        raise ValueError("nao achei marcacao de tempo no texto. "
                         "O formato e [mm:ss.cc] antes de cada linha")
    (pasta / "letra.lrc").write_text(texto, encoding="utf-8")
    return {"ok": True, "linhas": len(linhas)}


def sugerir_nomes(titulo: str) -> dict:
    """Artista e musica provaveis, para preencher os campos da pagina.

    A tag do arquivo vem primeiro, mas so quando existe de verdade: arquivo
    enviado pela pagina se chama pelo nome de origem e pode nao ter tag
    nenhuma. Nesse caso quem carrega a informacao e o nome da PASTA, nao o
    do arquivo.
    """
    from . import letras

    pasta = PASTA_SAIDA / titulo
    audio = _audio_da_pasta(pasta) if pasta.is_dir() else None
    if audio:
        try:
            from mutagen import File as MFile
            m = MFile(str(audio), easy=True)
            if m:
                t = (m.get("title") or [""])[0].strip()
                a = (m.get("artist") or [""])[0].strip()
                if t:
                    return {"titulo": letras.limpar_titulo(t),
                            "artista": letras.limpar_titulo(a)}
        except Exception as e:
            log.debug("Sem tags em %s: %s", audio.name, e)

    limpo = letras.limpar_titulo(titulo)
    if " - " in limpo:
        a, t = limpo.split(" - ", 1)
        return {"titulo": t.strip(), "artista": a.strip()}
    return {"titulo": limpo, "artista": ""}


def receber(nome: str, dados: bytes) -> str:
    """Grava um arquivo enviado pela pagina numa pasta propria.

    O nome de origem e preservado: e nele que costuma estar o artista e a
    musica, e a busca de letra depende disso.
    """
    limpo = nome_seguro(Path(nome).stem, 60)
    ext = Path(nome).suffix.lower()
    if ext not in AUDIO_EXT:
        raise ValueError("formato nao suportado: " + ext)
    pasta = PASTA_SAIDA / limpo
    if pasta.exists() and any(pasta.iterdir()):
        pasta = nome_unico(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / (limpo + ext)).write_bytes(dados)
    return pasta.name


# --------------------------------------------------------------------------- #
#  Servidor local
# --------------------------------------------------------------------------- #
_servidor = None
_porta = 0


class _Manipulador(http.server.SimpleHTTPRequestHandler):
    # No Windows o tipo de .js vem do registro e as vezes sai text/plain, o
    # que o navegador recusa para service worker. Fixa os que importam.
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".js": "text/javascript",
                      ".webmanifest": "application/manifest+json",
                      ".svg": "image/svg+xml",
                      ".mid": "audio/midi",
                      ".musicxml": "application/vnd.recordare.musicxml+xml"}

    # HTTP/1.1 mantem a conexao viva. Com 10 faixas, o limite de 6 conexoes
    # por host do navegador fazia as ultimas ficarem na fila e estourarem.
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, codigo=200):
        corpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    # ---------------------------------------------------------------- #
    #  Envio por faixa de bytes
    #
    #  O SimpleHTTPRequestHandler ignora o cabecalho Range e devolve o
    #  arquivo inteiro. Para audio isso e ruim de duas formas: o navegador
    #  precisa baixar tudo antes de tocar, e nao consegue procurar um ponto
    #  da musica. No modo Profundo, com dez FLAC de dezenas de MB abertos
    #  ao mesmo tempo, isso estoura a memoria do navegador e algumas faixas
    #  simplesmente nao carregam.
    # ---------------------------------------------------------------- #
    _RE_FAIXA = re.compile(r"bytes=(\d*)-(\d*)")

    def _enviar_com_faixa(self) -> bool:
        cabecalho = self.headers.get("Range")
        if not cabecalho:
            return False
        caminho = self.translate_path(self.path)
        if not os.path.isfile(caminho):
            return False
        m = self._RE_FAIXA.match(cabecalho.strip())
        if not m:
            return False

        tamanho = os.path.getsize(caminho)
        ini_txt, fim_txt = m.group(1), m.group(2)
        if ini_txt == "" and fim_txt == "":
            return False
        if ini_txt == "":                     # ultimos N bytes
            n = min(int(fim_txt), tamanho)
            ini, fim = tamanho - n, tamanho - 1
        else:
            ini = int(ini_txt)
            fim = int(fim_txt) if fim_txt else tamanho - 1
        fim = min(fim, tamanho - 1)

        if ini >= tamanho or ini > fim:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{tamanho}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True

        quantos = fim - ini + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(caminho))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {ini}-{fim}/{tamanho}")
        self.send_header("Content-Length", str(quantos))
        self.end_headers()
        try:
            with open(caminho, "rb") as f:
                f.seek(ini)
                restam = quantos
                while restam > 0:
                    bloco = f.read(min(64 * 1024, restam))
                    if not bloco:
                        break
                    self.wfile.write(bloco)
                    restam -= len(bloco)
        except (BrokenPipeError, ConnectionResetError):
            pass                              # o navegador cancelou; normal
        return True

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def do_GET(self):
        rota = urllib.parse.urlparse(self.path).path
        try:
            if rota == "/api/fila":
                return self._json({"fila": varrer(), "modos": _modos_com_tempo()})
            if rota == "/api/estado":
                return self._json(estado())
            if rota.startswith("/api/nomes"):
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                return self._json(sugerir_nomes(qs.get("titulo", [""])[0]))
            if rota == "/api/versao":
                from . import atualizar
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                return self._json(atualizar.verificar(forcar="forcar" in qs))
            if rota == "/api/atualizacao":
                from . import atualizar
                return self._json(atualizar.estado())
            if rota == "/api/partitura":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                d = partitura_completa(qs.get("titulo", [""])[0])
                return self._json(d if d else {"erro": "sem partitura"},
                                  200 if d else 404)
            if rota == "/api/partitura_zip":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                titulo = qs.get("titulo", [""])[0]
                tipo = qs.get("tipo", ["pdf"])[0]
                corpo = zip_partituras(titulo, tipo)
                if corpo is None:
                    return self._json({"erro": "nada para baixar"}, 404)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(corpo)))
                self.send_header("Content-Disposition",
                                 "attachment; filename*=UTF-8''"
                                 + urllib.parse.quote(f"{titulo} - partituras {tipo}.zip"))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(corpo)
                return
            if rota.startswith("/api/mapa"):
                from . import mapa as _mapa
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                titulo = qs.get("titulo", [""])[0]
                achado = next((m for m in varrer() if m["titulo"] == titulo), None)
                if not achado or not achado["temFaixas"]:
                    return self._json({"canais": [], "duracao": 0})
                return self._json(_mapa.calcular(titulo, achado["canais"]))
        except Exception as e:
            log.error("API GET %s: %s", rota, e, exc_info=True)
            return self._json({"erro": str(e)}, 500)
        try:
            if self._enviar_com_faixa():
                return
        except Exception as e:
            log.debug("Faixa de bytes falhou, mandando inteiro: %s", e)
        return super().do_GET()

    def _origem_confiavel(self) -> bool:
        """So aceita POST vindo da propria pagina. Sem isso, qualquer site
        aberto no navegador poderia mandar POST para 127.0.0.1 e, por exemplo,
        disparar uma atualizacao ou enfileirar separacoes. Confere o Host
        (contra DNS rebinding) e, quando o navegador manda, o Origin."""
        aceitos = {f"127.0.0.1:{_porta}", f"localhost:{_porta}"}
        if (self.headers.get("Host") or "").lower() not in aceitos:
            return False
        origem = self.headers.get("Origin")
        return origem is None or origem.lower() in {f"http://{h}" for h in aceitos}

    def do_POST(self):
        if not self._origem_confiavel():
            return self._json({"erro": "origem nao permitida"}, 403)
        rota = urllib.parse.urlparse(self.path).path
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else b""
        try:
            if rota == "/api/separar":
                dados = json.loads(corpo or b"{}")
                return self._json(enfileirar(dados.get("titulo", ""),
                                             dados.get("modo", "padrao")))
            if rota == "/api/cancelar":
                return self._json(cancelar())
            if rota == "/api/atualizar":
                from . import atualizar
                return self._json(atualizar.iniciar(ocupado=_ocupado))
            if rota == "/api/separar_vozes":
                d = json.loads(corpo or b"{}")
                return self._json(enfileirar(d.get("titulo", ""), "vozes",
                                             tipo="vozes"))
            if rota == "/api/separar_todas":
                d = json.loads(corpo or b"{}")
                return self._json(enfileirar_todas(d.get("modo", "padrao")))
            if rota == "/api/enviar":
                nome = urllib.parse.unquote(self.headers.get("X-Nome", "audio"))
                return self._json({"ok": True, "pasta": receber(nome, corpo)})
            if rota == "/api/letra":
                d = json.loads(corpo or b"{}")
                return self._json(buscar_letra(d.get("titulo", ""),
                                               d.get("artista", ""),
                                               d.get("musica", "")))
            if rota == "/api/letra_manual":
                d = json.loads(corpo or b"{}")
                return self._json(salvar_letra(d.get("titulo", ""),
                                               d.get("texto", "")))
        except Exception as e:
            log.error("API POST %s: %s", rota, e, exc_info=True)
            return self._json({"erro": str(e)}, 400)
        self.send_error(404)


def servidor_ligado() -> int:
    global _servidor, _porta
    if _servidor is not None:
        return _porta
    handler = functools.partial(_Manipulador, directory=str(PASTA_SAIDA))
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    for tentativa in (8137, 8138, 8139, 0):
        try:
            _servidor = socketserver.ThreadingTCPServer(("127.0.0.1", tentativa),
                                                        handler)
            break
        except OSError:
            continue
    if _servidor is None:
        raise RuntimeError("nao consegui abrir uma porta local")
    _porta = _servidor.server_address[1]
    threading.Thread(target=_servidor.serve_forever, daemon=True).start()
    log.info("Servidor da festa em http://127.0.0.1:%d", _porta)
    return _porta


def endereco() -> str:
    return f"http://127.0.0.1:{servidor_ligado()}/festa.html"


def gerar(destino: Path | None = None) -> tuple[Path, list[dict]]:
    fila = varrer()
    destino = destino or (PASTA_SAIDA / "festa.html")
    try:
        from . import pwa
        pwa.escrever(destino.parent)
    except Exception as e:                    # instalar e extra; nunca derruba a festa
        log.warning("Nao gravei os arquivos de instalacao: %s", e)
    destino.write_text(
        PAGINA.replace("__FILA__", json.dumps(fila, ensure_ascii=False))
              .replace("__MODOS__", json.dumps(_modos_com_tempo(),
                                               ensure_ascii=False)),
        encoding="utf-8")
    return destino, fila


PAGINA = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0b0d10">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" type="image/png" href="/icone-192.png">
<title>Modo festa</title>
<style>
  :root{
    --fundo:#0b0d10; --painel:#141920; --linha:#242c36;
    --texto:#e8edf2; --fraco:#7e8b99;
    --quente:#ff8a2b; --frio:#28d1c4; --alerta:#e2574c;
    --fonte:"Segoe UI",system-ui,-apple-system,Roboto,Helvetica,Arial,sans-serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;overflow:hidden}
  body{background:var(--fundo);color:var(--texto);font-family:var(--fonte);
       display:flex;flex-direction:column;user-select:none}

  header{display:flex;align-items:center;gap:16px;padding:12px 20px;
         border-bottom:1px solid var(--linha);flex-shrink:0}
  header h1{font-size:14px;font-weight:700;letter-spacing:2px;margin:0;
            color:var(--fraco);text-transform:uppercase}
  header .agora{font-size:17px;font-weight:600;flex:1;
                overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .selo{font-size:11px;padding:3px 9px;border-radius:99px;border:1px solid var(--linha);
        color:var(--fraco);white-space:nowrap}
  .selo.ok{color:var(--frio);border-color:var(--frio)}
  .selo.vivo{color:var(--quente);border-color:var(--quente)}

  main{flex:1;display:flex;min-height:0}

  /* ---- fila ---- */
  #fila{width:250px;border-right:1px solid var(--linha);overflow-y:auto;
        background:var(--painel);flex-shrink:0}
  #fila h2{font-size:11px;letter-spacing:1.5px;color:var(--fraco);margin:0;
           padding:14px 16px 8px;text-transform:uppercase}
  .item{padding:10px 16px;cursor:pointer;border-left:3px solid transparent;
        font-size:14px;line-height:1.35}
  .item:hover{background:#1a212a}
  .item.ativo{border-left-color:var(--quente);background:#1d242e}
  .item small{display:block;color:var(--fraco);font-size:11px;margin-top:2px}
  .item .sep{margin-top:6px;display:flex;gap:6px;align-items:center}
  .btsep{background:#0f141a;border:1px solid var(--quente);color:var(--quente);
         font:inherit;font-size:11px;padding:3px 9px;border-radius:4px;cursor:pointer}
  .btsep:hover{background:var(--quente);color:#1a0e02}
  .btsep[disabled]{opacity:.45;cursor:default;border-color:var(--linha);color:var(--fraco)}

  /* ---- abas da barra lateral ---- */
  .abas{display:flex;border-bottom:1px solid var(--linha);flex-shrink:0}
  .aba{flex:1;background:none;border:0;border-bottom:2px solid transparent;
       color:var(--fraco);font:inherit;font-size:11.5px;letter-spacing:.6px;
       padding:11px 4px;cursor:pointer;text-transform:uppercase}
  .aba:hover{color:var(--texto)}
  .aba.on{color:var(--quente);border-bottom-color:var(--quente)}
  .aba .marca{color:var(--quente);font-size:10px;margin-left:3px;letter-spacing:0}
  .painel[hidden]{display:none}
  #painel-fila{padding-top:4px}

  #painelSep{padding:12px 16px}
  #painelSep label{display:block;font-size:11px;letter-spacing:1.2px;
                   color:var(--fraco);margin-bottom:6px;text-transform:uppercase}
  #modoSep{width:100%;background:#0f141a;color:var(--texto);border:1px solid var(--linha);
           border-radius:5px;padding:7px;font:inherit;font-size:12.5px}
  #largar{margin-top:10px;border:1px dashed var(--linha);border-radius:6px;
          padding:12px;text-align:center;font-size:11.5px;color:var(--fraco);cursor:pointer}
  #largar.sobre{border-color:var(--frio);color:var(--frio)}
  #progresso{display:none;margin-top:10px;font-size:12px}
  #progresso .nome{color:var(--quente);font-weight:600}
  #progresso .trilho{height:5px;background:#05070a;border-radius:3px;margin:6px 0 4px;
                     position:relative;overflow:hidden}
  #progresso .trilho i{position:absolute;inset:0 auto 0 0;width:0;
                       background:var(--quente);border-radius:3px;transition:width .4s}
  #progresso .detalhe,#painelSep .detalhe{color:var(--fraco);font-size:11px;line-height:1.45}
  #progresso .cuidado{color:var(--alerta);font-size:11px;margin-top:6px;line-height:1.4}

  .btlet{background:#0f141a;border:1px solid var(--frio);color:var(--frio);
         font:inherit;font-size:11px;padding:3px 9px;border-radius:4px;cursor:pointer}
  .btvoz{background:#0f141a;border:1px solid #9a7bd1;color:#b79ae4;
         font:inherit;font-size:11px;padding:3px 9px;border-radius:4px;cursor:pointer}
  .btvoz:hover{background:#9a7bd1;color:#120a1e}
  .btvoz[disabled]{opacity:.45;cursor:default;border-color:var(--linha);color:var(--fraco)}
  .item .sep{flex-wrap:wrap}
  .btlet:hover{background:var(--frio);color:#04211f}
  #painelLetra{padding:14px 16px}

  #painel-ajuda{padding:14px 16px 24px;font-size:12.5px;line-height:1.6;
                color:#b9c3cd}
  #painel-ajuda h3{font-size:11.5px;letter-spacing:1.2px;color:var(--quente);
                   text-transform:uppercase;margin:18px 0 7px}
  #painel-ajuda h3:first-child{margin-top:0}
  #painel-ajuda p{margin:0 0 9px}
  #painel-ajuda ol,#painel-ajuda ul{margin:0 0 10px;padding-left:19px}
  #painel-ajuda li{margin-bottom:6px}
  #painel-ajuda b{color:var(--texto)}
  #painel-ajuda .destaque{background:#11181f;border-left:2px solid var(--frio);
                          padding:9px 11px;border-radius:0 5px 5px 0;margin:0 0 11px}
  #painel-ajuda .aviso2{background:#1a1310;border-left:2px solid var(--alerta);
                        padding:9px 11px;border-radius:0 5px 5px 0;margin:0 0 11px;
                        color:#d8a79e}
  #painel-ajuda table{width:100%;border-collapse:collapse;margin:0 0 11px;font-size:12px}
  #painel-ajuda td{padding:4px 0;border-bottom:1px solid var(--linha);vertical-align:top}
  #painel-ajuda td:first-child{color:var(--frio);white-space:nowrap;padding-right:10px}
  #painel-ajuda kbd{background:#0b1015;border:1px solid var(--linha);border-radius:3px;
                    padding:1px 5px;font-family:Consolas,monospace;font-size:11px;
                    color:var(--texto)}
  #painelLetra .alvo{font-size:12px;color:var(--frio);font-weight:600;margin-bottom:8px}
  #painelLetra input,#painelLetra textarea{width:100%;background:#05070a;color:var(--texto);
    border:1px solid var(--linha);border-radius:4px;padding:6px;font:inherit;
    font-size:12px;margin-bottom:6px}
  #painelLetra textarea{height:74px;resize:vertical;font-family:Consolas,monospace;font-size:11px}
  #painelLetra .fila2{display:flex;gap:6px;margin-bottom:6px}
  #painelLetra .fila2 button{flex:1}
  #letraMsg{font-size:11px;line-height:1.45;margin-top:4px}
  #letraMsg.bom{color:var(--frio)} #letraMsg.ruim{color:var(--alerta)}
  #painelLetra .sep2{border-top:1px solid var(--linha);margin:9px 0 7px;
                     padding-top:7px;font-size:11px;color:var(--fraco)}

  /* ---- centro ---- */
  #centro{flex:1;display:flex;flex-direction:column;min-width:0;padding:18px 22px;gap:14px}
  #espectro,#mapa{width:100%;height:150px;background:#080a0d;border-radius:8px;
                  border:1px solid var(--linha);display:block}
  #espectro{cursor:crosshair}
  #mapa{height:190px;cursor:pointer;display:none}
  body.verMapa #espectro,body.verMapa .dica{display:none}
  body.verMapa #mapa{display:block}
  body.semEspectro #mapa{display:none}
  .dica{font-size:11.5px;color:var(--fraco);margin:-8px 0 0}

  #letra{flex:1;overflow-y:auto;scroll-behavior:smooth;min-height:0;padding:6px 0}
  #letra p{margin:0 0 .5em;font-size:21px;line-height:1.45;color:#4d5864;
           transition:color .15s}
  #letra p.passou{color:#626e7b}
  #letra p.atual{color:var(--quente);font-size:29px;font-weight:700}
  #letra.vazio{display:flex;align-items:center;justify-content:center;
               color:var(--fraco);font-size:14px;text-align:center}

  /* ---- voz ---- */
  #voz{width:230px;border-left:1px solid var(--linha);background:var(--painel);
       padding:18px 16px;overflow-y:auto;flex-shrink:0}
  #voz h2,#canais h2{font-size:11px;letter-spacing:1.5px;color:var(--fraco);
                     margin:0 0 12px;text-transform:uppercase}
  .botaoVoz{width:100%;padding:16px;margin-bottom:9px;border-radius:9px;
            border:1px solid var(--linha);background:#0f141a;color:var(--texto);
            font:inherit;font-size:15px;font-weight:600;cursor:pointer;
            display:flex;justify-content:space-between;align-items:center}
  .botaoVoz:hover{border-color:var(--fraco)}
  .botaoVoz.on{background:var(--quente);border-color:var(--quente);color:#1a0e02}
  .botaoVoz span{font-size:12px;font-weight:500;opacity:.8}
  #canais{margin-top:22px}
  .canal{margin-bottom:11px}
  .canal .topo{display:flex;justify-content:space-between;font-size:13px}
  .canal .db{color:var(--fraco);font-size:11px;font-variant-numeric:tabular-nums}
  .canal input{width:100%;margin-top:4px;accent-color:var(--frio)}
  .canal.ev .topo{color:var(--quente)}
  .canal .luz{display:inline-block;width:7px;height:7px;border-radius:50%;
              margin-right:6px;background:var(--linha);vertical-align:middle}
  .canal.carregando .luz{background:var(--fraco);
                         animation:pisca 1s ease-in-out infinite}
  .canal.pronto .luz{background:var(--frio)}
  .canal.falhou .luz{background:var(--alerta)}
  .canal.falhou .topo{color:var(--alerta)}
  @keyframes pisca{0%,100%{opacity:.25}50%{opacity:1}}
  /* so aparece no canal que falhou */
  .canal .refazer{display:none;background:none;border:1px solid var(--alerta);
                  color:var(--alerta);font:inherit;font-size:10px;padding:1px 6px;
                  border-radius:3px;cursor:pointer;margin-left:6px}
  .canal.falhou .refazer{display:inline-block}
  .canal.falhou .refazer:hover{background:var(--alerta);color:#170b09}

  /* ---- transporte ---- */
  footer{border-top:1px solid var(--linha);background:var(--painel);
         padding:12px 20px 14px;display:flex;flex-direction:column;gap:11px;flex-shrink:0}
  .barra{height:7px;background:#05070a;border-radius:4px;cursor:pointer;position:relative}
  .barra i{position:absolute;inset:0 auto 0 0;width:0;background:var(--frio);border-radius:4px}
  .controles{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  button.bt{background:#0f141a;border:1px solid var(--linha);color:var(--texto);
            font:inherit;font-size:13px;padding:9px 15px;border-radius:6px;cursor:pointer}
  button.bt:hover{border-color:var(--fraco)}
  button.bt.on{border-color:var(--quente);color:var(--quente)}
  button.grande{background:var(--frio);color:#04211f;border:0;font-weight:700;
                padding:11px 30px;font-size:15px;min-width:118px}
  #btInicio,#btVolta10,#btFrente10{font-variant-numeric:tabular-nums;
                                   min-width:62px;text-align:center}
  #btInicio{min-width:40px}
  .relogio{color:var(--fraco);font-size:13px;font-variant-numeric:tabular-nums;min-width:104px}
  .grupo{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--fraco)}
  .grupo input[type=range]{width:110px;accent-color:var(--fraco)}
  .val{color:var(--texto);font-variant-numeric:tabular-nums;min-width:46px}
  #aviso{color:var(--alerta);font-size:12.5px;min-height:16px}

  /* ---- pontuacao ---- */
  #placar{display:none;align-items:center;gap:14px;padding:9px 0 2px}
  #placar.on{display:flex}
  #placar .nota{font-size:30px;font-weight:700;color:var(--quente);
                font-variant-numeric:tabular-nums;min-width:74px}
  #placar .rot{font-size:10.5px;letter-spacing:1.3px;color:var(--fraco);
               text-transform:uppercase}
  #afinador{flex:1;height:26px;background:#05070a;border-radius:5px;position:relative;
            overflow:hidden;min-width:150px}
  #afinador .centro{position:absolute;left:50%;top:0;bottom:0;width:2px;
                    background:var(--linha)}
  #afinador .agulha{position:absolute;top:3px;bottom:3px;width:5px;border-radius:3px;
                    background:var(--fraco);left:50%;transition:left .09s linear,
                    background .09s linear}
  #afinador .agulha.certo{background:var(--frio)}
  #afinador .agulha.perto{background:var(--quente)}
  #placar .seq{font-size:12.5px;color:var(--fraco);min-width:92px}
  #placar .seq b{color:var(--frio);font-size:15px}
  #fimMusica{display:none;font-size:13px;color:var(--frio);padding:6px 0 0}

  body.semEspectro #espectro,body.semEspectro .dica{display:none}
  body.semEspectro #letra p{font-size:25px}
  body.semEspectro #letra p.atual{font-size:36px}
  body.cheia #fila,body.cheia #voz,body.cheia #espectro,body.cheia .dica{display:none}
  body.cheia #letra p{font-size:30px}
  body.cheia #letra p.atual{font-size:46px}

  /* ---- partitura ---- */
  #partitura{display:none;flex:1;flex-direction:column;min-height:0;gap:8px}
  body.verPartitura #partitura{display:flex}
  body.verPartitura #espectro,body.verPartitura #mapa,
  body.verPartitura .dica,body.verPartitura #letra{display:none !important}
  #partBarra{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
  #partChips{display:flex;gap:6px;flex-wrap:wrap}
  .chip{background:#0f141a;border:1px solid var(--linha);color:var(--texto);
        font:inherit;font-size:13px;font-weight:600;padding:6px 14px;
        border-radius:99px;cursor:pointer}
  .chip:hover{border-color:var(--quente)}
  .chip.on{background:var(--quente);border-color:var(--quente);color:#1a0e02}
  #partInfo{font-size:12px;color:var(--fraco);flex:1;min-width:160px}
  .partCtl{font-size:11px;color:var(--fraco);display:flex;align-items:center;gap:6px}
  .partCtl input{width:90px;accent-color:var(--frio)}
  .partCtl .val{min-width:44px;font-variant-numeric:tabular-nums}
  #partBaixar{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
  #partBaixar a{text-decoration:none;display:inline-block}
  #partAviso{font-size:12px;line-height:1.4;color:var(--quente)}
  #partAviso.ruim{color:var(--alerta)}
  #partAviso:empty{display:none}
  #partFolha{flex:1;min-height:0;overflow:auto;background:#fbfaf7;border-radius:8px;
             border:1px solid var(--linha);user-select:none;position:relative}
  #partSvg{padding:10px 14px}
  #partSvg svg{display:block;margin:0 auto}
  #partSvg g.note{cursor:pointer}
  /* cabeca da nota e <use>: pinta por fill; haste e linha auxiliar, por color */
  #partSvg g.note.tocando,#partSvg g.note.tocando *{color:#e8590c;fill:#e8590c}
  #partSvg rect.confBaixa{fill:rgba(226,87,76,.20);pointer-events:none}
  #partSvg rect.confMedia{fill:rgba(255,176,32,.20);pointer-events:none}
  #partLetra{display:none;line-height:1.35}
  #partLetra .atual{font-size:22px;font-weight:700;color:var(--quente);min-height:1.35em}
  #partLetra .prox{font-size:15px;color:var(--fraco);min-height:1.35em}
  .partMsg{padding:40px 20px;text-align:center;color:#6b6b6b;font-size:14px}
  .verpart{background:none;border:1px solid var(--frio);color:var(--frio);
           font:inherit;font-size:10px;padding:1px 6px;border-radius:4px;
           margin-left:6px;cursor:pointer}
  .verpart:hover{background:var(--frio);color:#06201e}
  :focus-visible{outline:2px solid var(--quente);outline-offset:2px}
  @media (max-width:900px){
    main{flex-direction:column}
    #fila,#voz{width:100%;max-height:26vh;border:0;border-bottom:1px solid var(--linha)}
  }
</style>
</head>
<body>

<header>
  <h1>Festa</h1>
  <div class="agora" id="agora">Escolha uma musica</div>
  <button class="selo vivo" id="btInstalar" style="display:none;cursor:pointer;background:none"
          title="Abre o modo festa numa janela propria, como um aplicativo">Instalar app</button>
  <button class="selo vivo" id="btAtualizarApp" style="display:none;cursor:pointer;background:none"></button>
  <span class="selo" id="seloVersao" title="Versao instalada">v —</span>
  <span class="selo" id="selo">—</span>
</header>

<main>
  <section id="fila">
    <div class="abas">
      <button class="aba on" data-aba="fila">Fila</button>
      <button class="aba" data-aba="separar">Separar<span class="marca" id="marcaSep"></span></button>
      <button class="aba" data-aba="letra">Letra</button>
      <button class="aba" data-aba="ajuda">Ajuda</button>
    </div>

    <div class="painel" id="painel-fila">
      <button class="btsep" id="btAtualizar"
              style="width:calc(100% - 32px);margin:12px 16px 4px;padding:7px">
        Atualizar lista</button>
      <div id="lista"></div>
    </div>

    <div class="painel" id="painel-separar" hidden>
      <div id="painelSep">
        <label>Modo de separacao</label>
        <select id="modoSep"></select>
        <div id="largar">Arraste musicas aqui para acrescentar</div>
        <button class="btsep" id="btTodas" style="width:100%;margin-top:9px;padding:8px">
          Separar todas as pendentes</button>
        <button class="btlet" id="btCancelar" style="width:100%;margin-top:6px;padding:7px;
          display:none">Cancelar a fila</button>
        <div class="detalhe" id="avisoModo" style="margin-top:7px"></div>
        <div class="detalhe" id="pendentes" style="margin-top:5px"></div>
        <div id="progresso">
          <div class="nome" id="sepNome"></div>
          <div class="trilho"><i id="sepBarra"></i></div>
          <div class="detalhe" id="sepDetalhe"></div>
          <div class="cuidado">Separar enquanto toca disputa a CPU e pode
            falhar o audio. Prefira preparar antes da festa.</div>
        </div>
      </div>
    </div>

    <div class="painel" id="painel-ajuda" hidden>

      <h3>As tres visoes</h3>
      <p>O botao no rodape alterna entre elas:</p>
      <table>
        <tr><td>Espectro</td><td>o som ao vivo por frequencia. Arraste sobre ele
            para cortar ou isolar uma faixa de frequencia na hora</td></tr>
        <tr><td>Mapa</td><td>onde cada instrumento toca ao longo da musica, uma
            linha colorida por canal. Clique para pular para aquele ponto</td></tr>
        <tr><td>Oculto</td><td>esconde os dois e a letra fica maior</td></tr>
      </table>
      <p>No <b>mapa</b>, cor forte e o instrumento tocando alto, cor fraca e ele
         no fundo, vazio e silencio. De relance voce ve onde entra a voz, onde
         esta o solo e quando o baixo para.</p>
      <p>A medicao acontece na primeira vez que voce abre a musica e fica
         guardada. Da segunda em diante e instantanea.</p>

      <h3>Quando algo nao soa certo</h3>
      <p>Na pasta do projeto existem dois atalhos para isso:</p>
      <table>
        <tr><td>diagnostico.bat</td><td>lista, arquivo por arquivo, o que entra
            no mixer e por que alguma faixa ficou de fora</td></tr>
        <tr><td>faxina.bat</td><td>encontra faixas duplicadas e mixagens
            derivadas antigas, e pergunta antes de apagar</td></tr>
      </table>
      <p><b>Som metalico ou abafado</b> costuma ser a mesma faixa tocando duas
         vezes. Rode a faxina.</p>
      <p><b>Faltando instrumento</b>: veja a bolinha ao lado de cada canal.
         Verde carregou, vermelho falhou — e ai aparece um botao Refazer
         naquele canal.</p>
      <div class="destaque">
        <b>O teste que vale:</b> com todos os faders em 0 dB, a musica deve
        soar igual ao arquivo original. Se soar mais grave, mais alta ou
        metalica, tem faixa entrando em dobro.
      </div>

      <h3>Quanto tempo cada modo leva</h3>
      <p>Os numeros no seletor da aba Separar sao <b>medidos nesta maquina</b>,
         nao chutados. Enquanto um modo nunca foi rodado aqui, o tempo aparece
         com uma interrogacao e pode errar muito.</p>
      <table>
        <tr><td>Karaoke</td><td>2 faixas, uma passada</td></tr>
        <tr><td>Padrao</td><td>4 faixas, uma passada. O mais rapido e o
            recomendado</td></tr>
        <tr><td>Seis</td><td>6 faixas, uma passada. A qualidade do stem
            "outros" cai bastante</td></tr>
        <tr><td>Profundo</td><td>tres passadas em sequencia</td></tr>
      </table>
      <div class="aviso2">
        <b>Cuidado com o Profundo.</b> As duas passadas extras nao usam o mesmo
        modelo das 4 faixas: sao de outra familia, bem mais pesados. Sem placa
        de video eles podem custar <b>dez vezes</b> o tempo da primeira
        passada. Uma musica de 4 minutos pode passar de duas horas. Se voce so
        quer cantar, fique no Padrao.
      </div>
      <p>Se uma fila estiver demorando demais, o botao <b>Cancelar a fila</b>
         esvazia o que esta esperando. A musica que ja comecou termina: parar
         no meio deixaria arquivo pela metade.</p>

      <h3>Como tirar nota boa</h3>
      <p>O programa compara a <b>altura da nota</b> que voce canta com a da
         voz original, a cada 50 milissegundos. So isso. Saber as quatro
         regras abaixo e meio caminho andado.</p>

      <ol>
        <li><b>A oitava nao conta.</b> Homem cantando musica de mulher marca
            ponto cheio uma oitava abaixo. Cante no seu registro, sem forcar.</li>
        <li><b>Ate um semitom e ponto cheio.</b> Voce tem quase um tom inteiro
            de folga. Entre um e dois semitons vale meio ponto. Acima disso,
            zero.</li>
        <li><b>Trecho instrumental e de graca.</b> A conta so roda quando a voz
            original esta cantando. Intervalo nao desconta nada.</li>
        <li><b>Ficar calado e pior que desafinar.</b> Silencio onde a voz
            original canta vale zero. Meio tom errado vale meio ponto.</li>
      </ol>

      <div class="destaque">
        <b>O resumo:</b> cante sempre que a voz original cantar, com a nota
        parada dentro de um semitom. Nota firme vale mais que nota bonita.
      </div>

      <h3>O que derruba a nota</h3>
      <table>
        <tr><td>Vibrato largo</td><td>a nota oscila e sai da faixa de um semitom</td></tr>
        <tr><td>Deslizar ate a nota</td><td>o caminho ate ela conta como erro</td></tr>
        <tr><td>Improviso</td><td>qualquer nota diferente da original e erro</td></tr>
        <tr><td>Atrasar a entrada</td><td>silencio no comeco da frase vale zero</td></tr>
        <tr><td>Falar em vez de cantar</td><td>fala nao tem altura definida</td></tr>
      </table>

      <div class="aviso2">
        Isso e brincadeira, nao avaliacao. Quem canta bem, com vibrato e
        expressao, costuma pontuar <b>pior</b> que quem canta reto e sem graca.
        Maquina de karaoke profissional tem o mesmo defeito: ela mede afinacao,
        nao canto. Uma nota 45 quer dizer que voce passou de dois semitons na
        metade do tempo, nao que voce canta mal.
      </div>

      <h3>Para o placar funcionar direito</h3>
      <ul>
        <li>So vale em musica com <b>faixa de voz separada</b>: e dela que sai a
            nota de referencia. O botao fica bloqueado nas outras.</li>
        <li>Ligar a pontuacao <b>desliga a voz original</b>. Isso e proposital:
            se ela estivesse tocando, o microfone ouviria ela e o programa
            pontuaria a gravacao em vez de voce.</li>
        <li><b>Afaste o notebook das caixas.</b> O microfone capta o som da
            sala. O navegador tem cancelamento de eco, mas em volume de festa
            ele nao da conta sozinho e a nota sai generosa demais.</li>
        <li>Conversa, palmas e grito viram nota falsa. Quanto mais perto do
            microfone quem canta estiver, melhor.</li>
      </ul>

      <h3>O microfone de cantar</h3>
      <p>Ligue o microfone <b>direto na caixa</b> ou numa mesa de som, nunca no
         PC. Passando pelo computador, o Windows devolve o som com 100 a 250
         milissegundos de atraso e ninguem consegue cantar se ouvindo assim.</p>
      <p>O microfone do notebook serve so para medir. Ele nao devolve som
         nenhum para as caixas.</p>

      <h3>O afinador</h3>
      <p>A agulha mostra o quanto voce esta longe da nota original. No centro e
         acerto. Ela fica <b>verde</b> dentro de um semitom, <b>ambar</b> entre
         um e dois, e <b>cinza</b> quando erra ou quando voce nao esta cantando.
         Agulha para a direita, voce esta acima da nota; para a esquerda,
         abaixo.</p>

      <h3>Atalhos</h3>
      <table>
        <tr><td><kbd>espaco</kbd></td><td>toca e pausa</td></tr>
        <tr><td><kbd>&larr;</kbd> <kbd>&rarr;</kbd></td><td>volta e avanca 10 segundos</td></tr>
        <tr><td><kbd>0</kbd></td><td>volta ao comeco da musica</td></tr>
        <tr><td><kbd>&uarr;</kbd> <kbd>&darr;</kbd></td><td>muda o nivel da voz original</td></tr>
        <tr><td><kbd>n</kbd> <kbd>p</kbd></td><td>proxima e anterior</td></tr>
        <tr><td><kbd>e</kbd></td><td>alterna espectro, mapa e oculto</td></tr>
        <tr><td><kbd>f</kbd></td><td>tela cheia</td></tr>
      </table>

    </div>

    <div class="painel" id="painel-letra" hidden>
      <div id="painelLetra">
        <div class="alvo" id="letraAlvo">Escolha uma musica na aba Fila</div>
        <input id="letraArtista" placeholder="Artista">
        <input id="letraMusica" placeholder="Nome da musica">
        <div class="fila2">
          <button class="btsep" id="btBuscarLetra">Buscar</button>
          <button class="btsep" id="btFecharLetra">Voltar</button>
        </div>
        <div id="letraMsg"></div>
        <div class="sep2">Nao achou? Cole aqui um arquivo .lrc:</div>
        <textarea id="letraTexto" placeholder="[00:12.30] primeira linha"></textarea>
        <button class="btsep" id="btSalvarLetra">Gravar letra colada</button>
      </div>
    </div>
  </section>

  <section id="centro">
    <canvas id="espectro"></canvas>
    <canvas id="mapa"></canvas>
    <p class="dica" id="dica">Arraste sobre o espectro para marcar uma faixa
       de frequencia. Clique com o botao direito para soltar.</p>
    <p class="dica" id="dicaMapa" style="display:none">Cada faixa colorida e um
       instrumento. Quanto mais forte a cor, mais alto ele esta tocando.
       Clique para pular para aquele ponto.</p>
    <div id="letra" class="vazio">Sem letra sincronizada</div>
    <div id="partitura">
      <div id="partBarra">
        <div id="partChips"></div>
        <div id="partInfo"></div>
        <label class="partCtl" title="Adianta ou atrasa o cursor da partitura em relacao ao som">Ajuste
          <input type="range" id="partAjuste" min="-400" max="400" step="10" value="0">
          <span class="val" id="partAjusteV">0 ms</span></label>
        <label class="partCtl">Tamanho
          <input type="range" id="partZoom" min="60" max="160" step="10" value="100">
          <span class="val" id="partZoomV">100%</span></label>
        <div id="partBaixar"></div>
      </div>
      <div id="partAviso"></div>
      <div id="partLetra"><div class="atual">&nbsp;</div><div class="prox"></div></div>
      <div id="partFolha"><div id="partSvg"></div></div>
    </div>
  </section>

  <section id="voz">
    <h2>Voz original</h2>
    <div id="botoesVoz"></div>
    <div id="canais"><h2>Instrumentos</h2><div id="listaCanais"></div></div>
  </section>
</main>

<footer>
  <div class="barra" id="barra"><i id="preenche"></i></div>
  <div class="controles">
    <button class="bt" id="btInicio" title="Volta ao comeco da musica">|&lt;</button>
    <button class="bt" id="btVolta10">&lt;&lt; 10s</button>
    <button class="bt grande" id="btPlay">Tocar</button>
    <button class="bt" id="btFrente10">10s &gt;&gt;</button>
    <button class="bt" id="btAnt" title="Musica anterior">Anterior</button>
    <button class="bt" id="btProx" title="Proxima musica">Proxima</button>
    <span class="relogio" id="relogio">0:00 / 0:00</span>
    <span class="grupo">Filtro
      <select class="bt" id="modoFiltro">
        <option value="off">desligado</option>
        <option value="cortar">cortar faixa</option>
        <option value="isolar">isolar faixa</option>
      </select>
      <input type="range" id="forca" min="-40" max="12" step="1" value="-18">
      <span class="val" id="forcaV">-18 dB</span>
    </span>
    <button class="bt" id="btPontos">Pontuacao</button>
    <button class="bt on" id="btEspectro">Espectro</button>
    <button class="bt" id="btPartitura" style="display:none"
            title="Partitura da voz e do baixo, com cursor (tecla S)">Partitura</button>
    <button class="bt" id="btCheia">Tela cheia</button>
  </div>
  <div id="placar">
    <div>
      <div class="rot">Pontos</div>
      <div class="nota" id="notaAtual">0</div>
    </div>
    <div id="afinador">
      <div class="centro"></div>
      <div class="agulha" id="agulha"></div>
    </div>
    <div class="seq">sequencia<br><b id="seqAtual">0</b></div>
  </div>
  <div id="fimMusica"></div>
  <div id="aviso"></div>
</footer>

<script>
let FILA = __FILA__;
const MODOS = __MODOS__;

/* A pagina PRECISA ser servida por http. Aberta por file://, o navegador
   trata cada arquivo como origem isolada e o Web Audio devolve silencio
   em vez de erro, o que e pior do que falhar. Melhor barrar na entrada. */
if (location.protocol === 'file:') {
  document.body.innerHTML =
    '<div style="max-width:620px;margin:12vh auto;padding:0 24px;'
    + 'font:15px/1.65 Segoe UI,system-ui,sans-serif;color:#e8edf2">'
    + '<h1 style="color:#ff8a2b;font-size:20px">Abra pelo atalho, nao pelo arquivo</h1>'
    + '<p>Esta pagina foi aberta direto do disco. Nesse modo o navegador '
    + 'bloqueia o processamento de audio: o botao de voz, o espectro e os '
    + 'filtros ficariam mudos sem avisar.</p>'
    + '<p>Use um destes:</p>'
    + '<ul><li><b>festa.bat</b>, na pasta do projeto — abre sozinho</li>'
    + '<li>ou, com o programa aberto, a aba <b>Modo festa</b> e o botao '
    + '<b>Abrir a festa no navegador</b></li></ul>'
    + '<p style="color:#7e8b99">Os dois fazem a mesma coisa: sobem um '
    + 'servidor local e abrem o endereco 127.0.0.1.</p></div>';
  throw new Error('file:// nao suportado');
}

/* ============ audio ============ */
let ctx=null, bus=null, filtro=null, analisador=null;
let canais=[], ganhos=[], mestre=null, tocando=false, idx=-1;
let vozNivel=100, bandaIni=null, bandaFim=null;
const aviso=document.getElementById('aviso');

function criarContexto(){
  if (ctx) return;
  const AC = window.AudioContext || window.webkitAudioContext;
  ctx = new AC();
  bus = ctx.createGain();
  filtro = ctx.createBiquadFilter();
  filtro.type='peaking'; filtro.frequency.value=1000; filtro.Q.value=1; filtro.gain.value=0;
  analisador = ctx.createAnalyser();
  analisador.fftSize=4096; analisador.smoothingTimeConstant=0.72;
  bus.connect(filtro); filtro.connect(analisador); analisador.connect(ctx.destination);
  desenhar();
}

function limpar(){
  if(pontuando){ pontuando=false; desligarPontuacao();
                 document.getElementById('btPontos').classList.remove('on'); }
  canais.forEach(c=>{ try{c.el.pause(); c.el.src='';}catch(e){} });
  canais=[]; ganhos=[]; mestre=null; tocando=false;
  document.getElementById('btPlay').textContent='Tocar';
}

/* -------- motor 1: faixas separadas -------- */
/* Quantas faixas carregam ao mesmo tempo. Com 12 canais, mandar todas de
   uma vez estoura o navegador: ele abre doze decodificadores de audio e,
   com pouca memoria, desiste de algumas sem avisar direito. Em fila de
   tres, todas entram. */
const CARGA_SIMULTANEA = 3;

function marcarCanal(i, estado){
  const d=document.querySelector('.canal[data-idx="'+i+'"]');
  if(!d) return;
  d.classList.remove('carregando','pronto','falhou');
  d.classList.add(estado);
}

function carregarFaixa(o, i){
  return new Promise(resolve=>{
    const el=o.el;
    let fim=false;
    const pronto=()=>{ if(fim) return; fim=true;
                       marcarCanal(i,'pronto'); resolve(true); };
    const falhou=()=>{ if(fim) return; fim=true;
                       marcarCanal(i,'falhou'); o.falhou=true;
                       naoCarregaram.push(o.dados.arquivo); resolve(false); };
    marcarCanal(i,'carregando');
    el.addEventListener('loadedmetadata', pronto, {once:true});
    el.addEventListener('error', ()=>{
      const n=+(el.dataset.tentativas||0);
      if(n<2){ el.dataset.tentativas=String(n+1);
               setTimeout(()=>el.load(), 500*(n+1)); }
      else falhou();
    });
    setTimeout(()=>{ if(!fim && el.readyState>=1) pronto();
                     else if(!fim) falhou(); }, 20000);
    el.preload='metadata';
    el.load();
  });
}

let naoCarregaram=[];

async function carregarTodas(){
  naoCarregaram=[];
  const fila=canais.map((o,i)=>({o,i}));
  let ponteiro=0;
  async function trabalhador(){
    while(ponteiro<fila.length){
      const {o,i}=fila[ponteiro++];
      await carregarFaixa(o,i);
    }
  }
  await Promise.all(Array.from({length:Math.min(CARGA_SIMULTANEA,fila.length)},
                               trabalhador));
  const bons=canais.filter(o=>!o.falhou);
  if(bons.length) mestre=bons.reduce((a,b)=>
                    (b.el.duration||0)>(a.el.duration||0)?b:a, bons[0]).el;
  if(naoCarregaram.length){
    aviso.textContent='Nao carregaram: '+naoCarregaram.join(', ')
      +'  — clique em Refazer no canal em vermelho.';
  }
  return bons.length;
}

function montarComFaixas(m){
  m.canais.forEach((c)=>{
    /* encodeURIComponent por segmento: encodeURI deixa # e ? passarem,
       e um desses no nome da pasta truncava a URL. */
    const url=c.arquivo.split('/').map(encodeURIComponent).join('/');
    const el=new Audio();
    el.preload='none';       /* so carrega quando a fila mandar */
    el.src=url;
    if('preservesPitch' in el) el.preservesPitch=true;
    el.dataset.tentativas='0';
    const src=ctx.createMediaElementSource(el);
    const g=ctx.createGain(); g.gain.value=1;
    src.connect(g); g.connect(bus);
    /* A referencia de afinacao sai ANTES do ganho: assim a voz original
       continua servindo de gabarito mesmo com o botao de voz em 0%. */
    if(c.voz && !refVoz){
      refVoz=ctx.createAnalyser(); refVoz.fftSize=2048;
      src.connect(refVoz);
    }
    canais.push({el,dados:c,falhou:false}); ganhos.push(g);
  });
  mestre = canais[0].el;
}

/* -------- motor 2: cancelamento de centro ao vivo --------
   original -> passa-baixa 150 Hz ....................... grave intacto
   original -> passa-alta 8 kHz ......................... agudo intacto
   original -> banda da voz -> ganho (1-k) .............. parte que fica
   (L-R)    -> banda da voz -> ganho (k) ................ parte sem centro   */
let ganhoOriginal=null, ganhoLado=null, refVoz=null;
function montarSemFaixas(m){
  const el=new Audio(m.unico.split('/').map(encodeURIComponent).join('/'));
  el.preload='metadata';
  if('preservesPitch' in el) el.preservesPitch=true;
  el.addEventListener('error',()=>aviso.textContent='Nao carregou: '+m.unico);
  const src=ctx.createMediaElementSource(el);

  const grave=ctx.createBiquadFilter(); grave.type='lowpass'; grave.frequency.value=150;
  const agudo=ctx.createBiquadFilter(); agudo.type='highpass'; agudo.frequency.value=8000;
  src.connect(grave); grave.connect(bus);
  src.connect(agudo); agudo.connect(bus);

  function banda(entrada){
    const hp=ctx.createBiquadFilter(); hp.type='highpass'; hp.frequency.value=150;
    const lp=ctx.createBiquadFilter(); lp.type='lowpass';  lp.frequency.value=8000;
    entrada.connect(hp); hp.connect(lp); return lp;
  }
  ganhoOriginal=ctx.createGain();
  banda(src).connect(ganhoOriginal); ganhoOriginal.connect(bus);

  // lado = L - R, feito com separador de canais e ganho negativo
  const sep=ctx.createChannelSplitter(2);
  const jun=ctx.createGain();
  src.connect(sep);
  const gL=ctx.createGain(), gR=ctx.createGain();
  gL.gain.value=0.5; gR.gain.value=-0.5;
  sep.connect(gL,0); sep.connect(gR,1);
  gL.connect(jun); gR.connect(jun);
  ganhoLado=ctx.createGain();
  banda(jun).connect(ganhoLado); ganhoLado.connect(bus);

  canais.push({el,dados:{nome:'musica',chave:'unico',voz:false}});
  ganhos.push(null);
  mestre=el;
  aplicarVoz();
}

/* ============ botao de voz ============ */
const NIVEIS=[{v:100,r:'Original'},{v:75,r:'Reduz 25%'},
              {v:50,r:'Reduz 50%'},{v:0,r:'So instrumental'}];
function montarBotoesVoz(){
  const cx=document.getElementById('botoesVoz'); cx.innerHTML='';
  NIVEIS.forEach(n=>{
    const b=document.createElement('button');
    b.className='botaoVoz'+(n.v===vozNivel?' on':'');
    b.innerHTML='Voz '+n.v+'%<span>'+n.r+'</span>';
    b.onclick=()=>{ vozNivel=n.v; montarBotoesVoz(); aplicarVoz(); };
    cx.appendChild(b);
  });
}

function aplicarVoz(){
  const k = 1 - vozNivel/100;            // quanto tirar
  if (ganhoOriginal && ganhoLado){       // motor 2
    ganhoOriginal.gain.value = 1-k;
    ganhoLado.gain.value = k;
    return;
  }
  canais.forEach((c,i)=>{                // motor 1
    if (!c.dados.voz || !ganhos[i]) return;
    ganhos[i].gain.value = vozNivel/100;
    const sl=document.querySelector('[data-vol="'+i+'"]');
    if (sl){ const db = vozNivel? Math.round(20*Math.log10(vozNivel/100)) : -60;
             sl.value=db; marcarDb(i,db); }
  });
}

/* ============ mixer ============ */
function marcarDb(i,db){
  const e=document.getElementById('db'+i);
  if(e) e.textContent = db<=-60?'mudo':db+' dB';
}
function montarCanais(m){
  const cx=document.getElementById('listaCanais'); cx.innerHTML='';
  document.getElementById('canais').style.display = m.temFaixas?'block':'none';
  if(!m.temFaixas) return;
  canais.forEach((c,i)=>{
    const d=document.createElement('div');
    d.className='canal'+(c.dados.voz?' ev':'');
    d.dataset.idx=i;
    d.innerHTML='<div class="topo"><span><i class="luz"></i>'+c.dados.nome
      +'<button class="refazer" data-refaz="'+i+'">Refazer</button>'
      +(partDoItem(m)&&partDoItem(m).stems[c.dados.chave]&&partDoItem(m).stems[c.dados.chave].tela
          ?'<button class="verpart" data-part="'+c.dados.chave+'">Partitura</button>':'')
      +'</span>'
      +'<span class="db" id="db'+i+'">0 dB</span></div>'
      +'<input type="range" min="-60" max="12" step="1" value="0" data-vol="'+i+'">';
    cx.appendChild(d);
  });
  cx.querySelectorAll('[data-part]').forEach(b=>{
    b.onclick=()=>abrirPartitura(b.dataset.part);
  });
  cx.querySelectorAll('[data-refaz]').forEach(b=>{
    b.onclick=async ()=>{
      const i=+b.dataset.refaz, o=canais[i];
      o.falhou=false; o.el.dataset.tentativas='0';
      o.el.load();
      await carregarFaixa(o,i);
      if(tocando && !o.falhou){
        try{ o.el.currentTime=mestre.currentTime; await o.el.play(); }catch(e){}
      }
    };
  });
  cx.querySelectorAll('[data-vol]').forEach(sl=>{
    sl.oninput=()=>{ const i=+sl.dataset.vol, db=+sl.value;
      if(ganhos[i]) ganhos[i].gain.value = db<=-60?0:Math.pow(10,db/20);
      marcarDb(i,db); };
  });
}

/* ============ fila ============ */
function montarFila(){
  const cx=document.getElementById('lista'); cx.innerHTML='';
  if(!FILA.length){
    cx.innerHTML='<div class="item"><small>Nada em saida/ ainda. '
                +'Arraste musicas no campo acima.</small></div>'; return; }
  FILA.forEach((m,i)=>{
    const d=document.createElement('div');
    d.className='item'+(i===idx?' ativo':''); d.dataset.i=i;
    let html=m.titulo+'<small>'+(m.temFaixas
      ? m.canais.length+' faixas'+(m.letra.length?' · letra':'')
      : 'sem faixas · cancelamento ao vivo')+'</small>';
    let acoes='';
    if(!m.temFaixas)
      acoes+='<button class="btsep" data-sep="'
            +encodeURIComponent(m.titulo)+'">Separar faixas</button>';
    if(!m.letra.length)
      acoes+='<button class="btlet" data-let="'
            +encodeURIComponent(m.titulo)+'">Buscar letra</button>';
    const temVoz=m.canais.some(c=>c.chave==='vocal'||c.chave==='vocal_principal');
    const jaDividiu=m.canais.some(c=>c.chave==='voz_masculina'||c.chave==='voz_feminina');
    if(temVoz && !jaDividiu)
      acoes+='<button class="btvoz" data-voz="'
            +encodeURIComponent(m.titulo)+'">Separar vozes</button>';
    if(acoes) html+='<div class="sep">'+acoes+'</div>';
    d.innerHTML=html;
    d.onclick=e=>{ if(e.target.classList.contains('btsep')) return;
                   carregar(i,true); };
    cx.appendChild(d);
  });
  cx.querySelectorAll('[data-voz]').forEach(b=>{
    const alvo=decodeURIComponent(b.dataset.voz);
    if(SEP.atual===alvo){ b.disabled=true; b.textContent='dividindo...'; }
    else if(SEP.espera.includes(alvo)){ b.disabled=true; b.textContent='na fila'; }
    b.onclick=async ev=>{
      ev.stopPropagation();
      if(!confirm('Dividir a voz em masculina e feminina?\n\n'
        +'Leva de 6 a 9 minutos nesta maquina e o resultado tem vazamento '
        +'audivel: funciona quando cada um canta um trecho, falha quando '
        +'cantam juntos. Duas vozes de timbre parecido nao separam.')) return;
      b.disabled=true; b.textContent='na fila...';
      try{
        const r=await fetch('/api/separar_vozes',{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({titulo:alvo})});
        const j=await r.json();
        if(j.erro){ aviso.textContent=j.erro;
                    b.disabled=false; b.textContent='Separar vozes'; }
      }catch(e){ aviso.textContent='Servidor nao respondeu.';
                 b.disabled=false; b.textContent='Separar vozes'; }
    };
  });
  cx.querySelectorAll('[data-let]').forEach(b=>{
    b.onclick=ev=>{ ev.stopPropagation();
                    abrirLetra(decodeURIComponent(b.dataset.let)); };
  });
  cx.querySelectorAll('[data-sep]').forEach(b=>{
    const alvo=decodeURIComponent(b.dataset.sep);
    if(SEP.atual===alvo){ b.disabled=true; b.textContent='separando...'; }
    else if(SEP.espera.includes(alvo)){ b.disabled=true; b.textContent='na fila'; }
    b.onclick=async ev=>{
      ev.stopPropagation();
      b.disabled=true; b.textContent='na fila...';
      try{
        const r=await fetch('/api/separar',{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({titulo:decodeURIComponent(b.dataset.sep),
                               modo:document.getElementById('modoSep').value})});
        const j=await r.json();
        if(j.erro){ aviso.textContent='Nao consegui separar: '+j.erro;
                    b.disabled=false; b.textContent='Separar faixas'; }
      }catch(e){ aviso.textContent='Servidor nao respondeu.';
                 b.disabled=false; b.textContent='Separar faixas'; }
    };
  });
}

/* ---------- separar em lote ---------- */
let SEP={atual:null,espera:[]};

function contarPendentes(){
  const n=FILA.filter(m=>!m.temFaixas).length;
  const bt=document.getElementById('btTodas');
  const txt=document.getElementById('pendentes');
  bt.disabled = n===0;
  bt.textContent = n ? 'Separar todas as pendentes ('+n+')'
                     : 'Nenhuma pendente';
  txt.textContent = n ? 'Vao rodar uma de cada vez, em sequencia. '
                        +'Com dois nucleos, em paralelo seria mais lento.' : '';
}

document.getElementById('btTodas').onclick=async ()=>{
  const bt=document.getElementById('btTodas');
  bt.disabled=true; bt.textContent='enfileirando...';
  try{
    const r=await fetch('/api/separar_todas',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({modo:document.getElementById('modoSep').value})});
    const j=await r.json();
    if(j.erro){ aviso.textContent=j.erro; }
    else document.getElementById('pendentes').textContent=
      j.postas+' musica(s) na fila. Acompanhe a barra abaixo.';
  }catch(e){ aviso.textContent='Servidor nao respondeu.'; }
  contarPendentes();
};

document.getElementById('btCancelar').onclick=async ()=>{
  if(!confirm('Esvaziar a fila?\n\nA musica que ja esta rodando nao para no '
     +'meio: ela termina e depois nada mais comeca.')) return;
  try{
    const j=await (await fetch('/api/cancelar',{method:'POST'})).json();
    document.getElementById('pendentes').textContent=
      j.removidas+' tirada(s) da fila.'
      +(j.terminando?' "'+j.terminando+'" esta terminando.':'');
  }catch(e){ aviso.textContent='Servidor nao respondeu.'; }
};

/* aviso de custo do modo escolhido */
function avisarModo(){
  const id=document.getElementById('modoSep').value;
  const m=MODOS.find(x=>x.id===id);
  const el=document.getElementById('avisoModo');
  if(!m){ el.textContent=''; return; }
  let txt='Estimativa para 4 minutos de musica: '+m.tempo+'.';
  if(!m.medido) txt+=' Ainda nao medido nesta maquina; o numero e chute e '
                    +'pode errar feio para mais.';
  if(id==='profundo') txt+=' O modo Profundo roda tres passadas, e as duas '
                          +'extras usam modelos de outra familia, bem mais '
                          +'pesados que o das 4 faixas.';
  el.textContent=txt;
}

/* ---------- abas da barra lateral ---------- */
function mostrarAba(nome){
  document.querySelectorAll('.aba').forEach(b=>
    b.classList.toggle('on', b.dataset.aba===nome));
  document.querySelectorAll('.painel').forEach(p=>
    p.hidden = (p.id !== 'painel-'+nome));
}
document.querySelectorAll('.aba').forEach(b=>
  b.onclick=()=>mostrarAba(b.dataset.aba));

document.getElementById('btAtualizar').onclick=async ()=>{
  const b=document.getElementById('btAtualizar');
  b.textContent='procurando...';
  const antes=FILA.length;
  await recarregarFila();
  const novas=FILA.length-antes;
  b.textContent='Atualizar lista';
  aviso.textContent = novas>0 ? novas+' musica(s) nova(s) na fila.'
                   : (FILA.length?'Nada novo.':'Nenhuma musica em saida.');
  setTimeout(()=>{ aviso.textContent=''; }, 4000);
};

/* ---------- letras ---------- */
let letraAtual=null;
const msgLetra=document.getElementById('letraMsg');

async function abrirLetra(titulo){
  letraAtual=titulo;
  document.getElementById('letraAlvo').textContent=titulo;
  msgLetra.textContent=''; msgLetra.className='';
  document.getElementById('letraTexto').value='';
  mostrarAba('letra');
  try{
    const j=await (await fetch('/api/nomes?titulo='+encodeURIComponent(titulo))).json();
    document.getElementById('letraArtista').value=j.artista||'';
    document.getElementById('letraMusica').value=j.titulo||'';
  }catch(e){}
}
document.getElementById('btFecharLetra').onclick=()=>mostrarAba('fila');

document.getElementById('btBuscarLetra').onclick=async ()=>{
  if(!letraAtual) return;
  msgLetra.className=''; msgLetra.textContent='Procurando no LRCLIB...';
  try{
    const r=await fetch('/api/letra',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({titulo:letraAtual,
        artista:document.getElementById('letraArtista').value,
        musica:document.getElementById('letraMusica').value})});
    const j=await r.json();
    if(j.ok){
      msgLetra.className='bom';
      msgLetra.textContent='Achei: '+j.artista+' - '+j.titulo
                          +' ('+j.linhas+' linhas)';
      await recarregarFila();
      if(idx>=0 && FILA[idx] && FILA[idx].titulo===letraAtual) montarLetra(FILA[idx]);
    } else {
      msgLetra.className='ruim'; msgLetra.textContent=j.motivo||j.erro||'nada encontrado';
    }
  }catch(e){ msgLetra.className='ruim'; msgLetra.textContent='Servidor nao respondeu.'; }
};

document.getElementById('btSalvarLetra').onclick=async ()=>{
  if(!letraAtual) return;
  try{
    const r=await fetch('/api/letra_manual',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({titulo:letraAtual,
                           texto:document.getElementById('letraTexto').value})});
    const j=await r.json();
    if(j.erro){ msgLetra.className='ruim'; msgLetra.textContent=j.erro; return; }
    msgLetra.className='bom'; msgLetra.textContent='Gravado ('+j.linhas+' linhas)';
    await recarregarFila();
    if(idx>=0 && FILA[idx] && FILA[idx].titulo===letraAtual) montarLetra(FILA[idx]);
  }catch(e){ msgLetra.className='ruim'; msgLetra.textContent='Servidor nao respondeu.'; }
};

/* ---------- seletor de modo ---------- */
(function(){
  const s=document.getElementById('modoSep');
  MODOS.forEach(m=>{
    const o=document.createElement('option');
    o.value=m.id;
    o.textContent='['+m.n+'] '+m.rotulo+'  '+m.faixas+'  '+m.tempo
                 +(m.recomendado?'  (recomendado)':'');
    if(m.recomendado) o.selected=true;
    s.appendChild(o);
  });
  s.onchange=avisarModo;
  avisarModo();
})();

/* ---------- acrescentar musicas ---------- */
const largar=document.getElementById('largar');
const escolher=document.createElement('input');
escolher.type='file'; escolher.multiple=true; escolher.accept='audio/*';
escolher.style.display='none'; document.body.appendChild(escolher);
largar.onclick=()=>escolher.click();
escolher.onchange=()=>enviarArquivos(escolher.files);
['dragenter','dragover'].forEach(ev=>largar.addEventListener(ev,e=>{
  e.preventDefault(); largar.classList.add('sobre'); }));
['dragleave','drop'].forEach(ev=>largar.addEventListener(ev,e=>{
  e.preventDefault(); largar.classList.remove('sobre'); }));
largar.addEventListener('drop',e=>enviarArquivos(e.dataTransfer.files));

async function enviarArquivos(arquivos){
  const lista=[...arquivos];
  if(!lista.length) return;
  for(let i=0;i<lista.length;i++){
    const f=lista[i];
    largar.textContent='Enviando '+(i+1)+' de '+lista.length+'...';
    try{
      const r=await fetch('/api/enviar',{method:'POST',
        headers:{'X-Nome':encodeURIComponent(f.name)}, body:f});
      const j=await r.json();
      if(j.erro) aviso.textContent=f.name+': '+j.erro;
    }catch(e){ aviso.textContent='Falha ao enviar '+f.name; }
  }
  largar.textContent='Arraste musicas aqui para acrescentar';
  await recarregarFila();
}

/* ---------- estado da separacao ---------- */
async function recarregarFila(){
  try{
    const r=await fetch('/api/fila'); const j=await r.json();
    const antes=idx>=0?FILA[idx].titulo:null;
    FILA=j.fila;
    if(antes){ const novo=FILA.findIndex(m=>m.titulo===antes);
               if(novo>=0) idx=novo; }
    montarFila(); contarPendentes();
  }catch(e){}
}

let ultimoFeito=0, assinaturaAtual=null;
setInterval(async ()=>{
  let j;
  try{ j=await (await fetch('/api/estado')).json(); }catch(e){ return; }
  const cx=document.getElementById('progresso');
  const marca=document.getElementById('marcaSep');
  SEP={atual:j.atual?j.atual.titulo:null, espera:j.espera||[]};
  document.getElementById('btCancelar').style.display=
    (j.atual || j.espera.length) ? 'block' : 'none';
  marca.textContent = j.atual ? Math.round(j.atual.frac*100)+'%'
                     : (j.espera.length ? '·'+j.espera.length : '');
  if(j.atual){
    cx.style.display='block';
    document.getElementById('sepNome').textContent=j.atual.titulo;
    document.getElementById('sepBarra').style.width=
      Math.round(j.atual.frac*100)+'%';
    const min=Math.floor(j.atual.decorrido/60), seg=j.atual.decorrido%60;
    document.getElementById('sepDetalhe').textContent=
      (j.atual.texto||'')+'  ·  '+min+':'+String(seg).padStart(2,'0')
      +(j.espera.length?'  ·  '+j.espera.length+' na espera':'');
  } else {
    cx.style.display = j.espera.length ? 'block' : 'none';
    if(j.espera.length){
      document.getElementById('sepNome').textContent='Na espera';
      document.getElementById('sepDetalhe').textContent=j.espera.join(', ');
      document.getElementById('sepBarra').style.width='0%';
    }
  }
  if(j.feitos.length!==ultimoFeito){ ultimoFeito=j.feitos.length; recarregarFila(); }
  /* musica que chegou por fora, descompactada na pasta saida */
  if(assinaturaAtual===null){ assinaturaAtual=j.assinatura; }
  else if(j.assinatura!==assinaturaAtual){
    assinaturaAtual=j.assinatura;
    recarregarFila();
    aviso.textContent='Fila atualizada: a pasta saida mudou.';
    setTimeout(()=>{ if(aviso.textContent.startsWith('Fila atualizada'))
                       aviso.textContent=''; }, 4000);
  }
  if(j.erros.length) aviso.textContent='Erro em '
    +j.erros[j.erros.length-1].titulo+': '+j.erros[j.erros.length-1].erro;
}, 1500);

function carregar(i,tocarJa){
  if(i<0||i>=FILA.length) return;
  criarContexto(); limpar();
  ganhoOriginal=ganhoLado=null; refVoz=null; zerarPontos();
  idx=i; const m=FILA[i];
  document.getElementById('agora').textContent=m.titulo;
  const selo=document.getElementById('selo');
  selo.textContent = m.temFaixas ? 'faixas separadas' : 'cancelamento ao vivo';
  selo.className = 'selo '+(m.temFaixas?'ok':'vivo');
  aviso.textContent = m.temFaixas ? ''
    : 'Sem faixas separadas: o cancelamento nao funciona em toda musica. '
     +'Separe antes para garantir.';
  document.querySelectorAll('.item').forEach(e=>
    e.classList.toggle('ativo', +e.dataset.i===i));

  if(m.temFaixas) montarComFaixas(m); else montarSemFaixas(m);
  montarCanais(m); montarBotoesVoz(); aplicarVoz(); montarLetra(m);
  prepararPartitura(m);
  carregarMapa(m.temFaixas?m.titulo:null);
  if(m.temFaixas){
    aviso.textContent='Carregando '+m.canais.length+' faixas...';
    carregarTodas().then(bons=>{
      if(!naoCarregaram.length) aviso.textContent='';
      if(tocarJa && bons) tocar();
    });
  } else if(tocarJa) tocar();
}

/* ============ letra ============ */
let linhas=[], ultima=-1;
function montarLetra(m){
  const cx=document.getElementById('letra'); linhas=[]; ultima=-1;
  if(!m.letra.length){ cx.className='vazio';
    cx.textContent='Sem letra sincronizada para esta musica';
    atualizarLetraPart(-1); return; }
  cx.className=''; cx.innerHTML='';
  m.letra.forEach(l=>{ const p=document.createElement('p');
    p.textContent=l.texto||'\u00a0'; p.onclick=()=>irPara(l.t);
    cx.appendChild(p); linhas.push({t:l.t,el:p}); });
  atualizarLetraPart(-1);
}
/* a aba Partitura esconde a letra grande; esta faixa mantem a linha atual
   e a proxima a vista de quem canta lendo a pauta */
function atualizarLetraPart(k){
  const cx=document.getElementById('partLetra');
  if(!linhas.length){ cx.style.display='none'; return; }
  cx.style.display='block';
  const txt=i=>(i>=0&&i<linhas.length)?linhas[i].el.textContent.trim():'';
  cx.children[0].textContent=txt(k)||'\u00a0';
  cx.children[1].textContent=txt(k+1);
}
function atualizarLetra(t){
  if(!linhas.length) return;
  let k=-1; for(let i=0;i<linhas.length;i++){ if(linhas[i].t<=t+0.15) k=i; else break; }
  if(k===ultima) return;
  linhas.forEach((l,i)=>{ l.el.classList.toggle('atual',i===k);
                          l.el.classList.toggle('passou',i<k); });
  if(k>=0){ const cx=document.getElementById('letra');
            cx.scrollTop=linhas[k].el.offsetTop-cx.clientHeight*0.36; }
  ultima=k;
  atualizarLetraPart(k);
}

/* ============ espectro e filtro ao vivo ============ */
const cv=document.getElementById('espectro'), cc=cv.getContext('2d');
function redimensionar(){ cv.width=cv.clientWidth*devicePixelRatio;
                          cv.height=cv.clientHeight*devicePixelRatio; }
addEventListener('resize',redimensionar); redimensionar();

function hzParaX(hz){
  const min=40, max=16000;
  return (Math.log(Math.max(hz,min)/min)/Math.log(max/min))*cv.width;
}
function xParaHz(x){
  const min=40, max=16000;
  return min*Math.pow(max/min, Math.min(Math.max(x,0),cv.width)/cv.width);
}

function desenhar(){
  requestAnimationFrame(desenhar);
  medir();
  if(document.body.classList.contains('verPartitura')) atualizarPartitura();
  if(document.body.classList.contains('verMapa')) desenharMapa();
  if(!analisador) return;
  const n=analisador.frequencyBinCount, dados=new Uint8Array(n);
  analisador.getByteFrequencyData(dados);
  cc.clearRect(0,0,cv.width,cv.height);

  if(bandaIni!==null&&bandaFim!==null){
    const a=Math.min(bandaIni,bandaFim), b=Math.max(bandaIni,bandaFim);
    cc.fillStyle='rgba(255,138,43,.16)'; cc.fillRect(a,0,b-a,cv.height);
    cc.strokeStyle='rgba(255,138,43,.6)'; cc.lineWidth=1*devicePixelRatio;
    cc.strokeRect(a,0,b-a,cv.height);
  }
  const taxa = ctx?ctx.sampleRate:44100;
  const larg = 2*devicePixelRatio;
  for(let x=0;x<cv.width;x+=larg){
    const hz=xParaHz(x);
    const bin=Math.min(n-1, Math.round(hz/(taxa/2)*n));
    const v=dados[bin]/255;
    const h=v*cv.height;
    const g=cc.createLinearGradient(0,cv.height,0,cv.height-h);
    g.addColorStop(0,'rgba(40,209,196,.85)'); g.addColorStop(1,'rgba(255,138,43,.95)');
    cc.fillStyle=g; cc.fillRect(x,cv.height-h,larg-devicePixelRatio,h);
  }
}

let arrastando=false;
cv.addEventListener('pointerdown',e=>{
  if(e.button!==0) return;
  arrastando=true; bandaIni=(e.offsetX)*devicePixelRatio; bandaFim=bandaIni;
  cv.setPointerCapture(e.pointerId);
});
cv.addEventListener('pointermove',e=>{ if(arrastando) bandaFim=e.offsetX*devicePixelRatio; });
cv.addEventListener('pointerup',()=>{ arrastando=false; aplicarFiltro(); });
cv.addEventListener('contextmenu',e=>{ e.preventDefault();
  bandaIni=bandaFim=null; aplicarFiltro(); });

const modoFiltro=document.getElementById('modoFiltro');
const forca=document.getElementById('forca'), forcaV=document.getElementById('forcaV');
modoFiltro.onchange=aplicarFiltro;
forca.oninput=()=>{ forcaV.textContent=forca.value+' dB'; aplicarFiltro(); };

function aplicarFiltro(){
  if(!filtro) return;
  const modo=modoFiltro.value;
  if(modo==='off'||bandaIni===null||bandaFim===null){
    filtro.type='peaking'; filtro.gain.value=0; filtro.frequency.value=1000;
    return;
  }
  const a=xParaHz(Math.min(bandaIni,bandaFim)), b=xParaHz(Math.max(bandaIni,bandaFim));
  const centro=Math.sqrt(a*b), q=Math.max(0.3, centro/Math.max(b-a,1));
  filtro.frequency.value=centro; filtro.Q.value=q;
  if(modo==='isolar'){ filtro.type='bandpass'; filtro.gain.value=0; }
  else { filtro.type='peaking'; filtro.gain.value=+forca.value; }
}

/* ============ mapa de atividade ============
   Mostra onde cada instrumento toca ao longo da musica, uma faixa colorida
   por canal. O calculo vem do servidor e fica em cache na pasta da musica,
   entao so a primeira abertura custa alguns segundos. */

const cvMapa=document.getElementById('mapa');
const ccMapa=cvMapa.getContext('2d');
let MAPA=null, mapaCarregando=false;

function dimensionarMapa(){
  cvMapa.width=cvMapa.clientWidth*devicePixelRatio;
  cvMapa.height=cvMapa.clientHeight*devicePixelRatio;
}
addEventListener('resize',dimensionarMapa);

async function carregarMapa(titulo){
  MAPA=null;
  if(!titulo) return;
  mapaCarregando=true; desenharMapa();
  try{
    const r=await fetch('/api/mapa?titulo='+encodeURIComponent(titulo));
    const j=await r.json();
    MAPA = (j.canais && j.canais.length) ? j : null;
  }catch(e){ MAPA=null; }
  mapaCarregando=false;
  dimensionarMapa(); desenharMapa();
}

function desenharMapa(){
  if(!cvMapa.width) dimensionarMapa();
  const L=cvMapa.width, A=cvMapa.height;
  ccMapa.clearRect(0,0,L,A);

  if(mapaCarregando){
    ccMapa.fillStyle='#7e8b99';
    ccMapa.font=(13*devicePixelRatio)+'px system-ui,sans-serif';
    ccMapa.fillText('Medindo as faixas...', 14*devicePixelRatio, A/2);
    return;
  }
  if(!MAPA){
    ccMapa.fillStyle='#7e8b99';
    ccMapa.font=(13*devicePixelRatio)+'px system-ui,sans-serif';
    ccMapa.fillText('Sem faixas separadas nesta musica.',
                    14*devicePixelRatio, A/2);
    return;
  }

  const n=MAPA.canais.length;
  const rotulo=96*devicePixelRatio;
  const alturaLinha=A/n;
  const dur=MAPA.duracao||1;
  const area=L-rotulo;

  MAPA.canais.forEach((c,i)=>{
    const y=i*alturaLinha;
    ccMapa.fillStyle = i%2 ? 'rgba(255,255,255,.02)' : 'transparent';
    ccMapa.fillRect(rotulo,y,area,alturaLinha);

    const passos=c.niveis.length||1;
    const larg=area/passos;
    for(let k=0;k<passos;k++){
      const v=c.niveis[k];
      if(v<=0.02) continue;
      ccMapa.globalAlpha=0.12+0.88*v;
      ccMapa.fillStyle=c.cor;
      const h=(alturaLinha-3*devicePixelRatio)*Math.max(0.18,v);
      ccMapa.fillRect(rotulo+k*larg,
                      y+(alturaLinha-h)/2,
                      Math.max(larg-0.4,0.6), h);
    }
    ccMapa.globalAlpha=1;

    ccMapa.fillStyle=c.cor;
    ccMapa.font=(11*devicePixelRatio)+'px system-ui,sans-serif';
    ccMapa.textBaseline='middle';
    const nome=c.nome.length>13?c.nome.slice(0,12)+'.':c.nome;
    ccMapa.fillText(nome, 9*devicePixelRatio, y+alturaLinha/2);
  });

  if(mestre && mestre.duration){
    const x=rotulo+(mestre.currentTime/dur)*area;
    ccMapa.strokeStyle='rgba(255,255,255,.75)';
    ccMapa.lineWidth=1.5*devicePixelRatio;
    ccMapa.beginPath(); ccMapa.moveTo(x,0); ccMapa.lineTo(x,A); ccMapa.stroke();
  }
}

cvMapa.addEventListener('click',e=>{
  if(!MAPA||!mestre) return;
  const rotulo=96, r=cvMapa.getBoundingClientRect();
  const x=e.clientX-r.left-rotulo;
  const area=r.width-rotulo;
  if(x<0||area<=0) return;
  irPara((x/area)*(MAPA.duracao||mestre.duration||0));
});

/* ============ pontuacao ============
   Compara a altura da nota que voce canta com a da voz original.
   A oitava e ignorada: homem cantando musica de mulher esta certo uma
   oitava abaixo. So conta quando a voz original esta cantando; trecho
   instrumental e de graca, e ficar calado onde tem voz desconta.

   A deteccao usa autocorrelacao normalizada e escolhe o MENOR atraso
   que chegue perto do pico. Sem essa regra a autocorrelacao acerta um
   multiplo do periodo e reporta uma oitava abaixo: 440 Hz vira 110. */

let pontuando=false, micNo=null, micAnal=null, micFluxo=null;
let acertos=0, total=0, seq=0, melhorSeq=0, ultimaMedida=0;

const bufRef=new Float32Array(2048), bufMic=new Float32Array(2048);

function detectarNota(buf, taxa){
  let soma=0;
  for(let i=0;i<buf.length;i++) soma+=buf[i]*buf[i];
  if(Math.sqrt(soma/buf.length) < 0.008) return -1;

  let media=0; for(let i=0;i<buf.length;i++) media+=buf[i];
  media/=buf.length;
  const x=new Float32Array(buf.length);
  for(let i=0;i<buf.length;i++) x[i]=buf[i]-media;

  const n=x.length;
  const minLag=Math.max(2,Math.floor(taxa/1000));
  const maxLag=Math.min(Math.floor(taxa/75), n>>1);
  if(maxLag<=minLag) return -1;

  const vals=new Float32Array(maxLag-minLag);
  for(let lag=minLag;lag<maxLag;lag++){
    let num=0,ea=0,eb=0;
    for(let i=0;i<n-lag;i++){ const a=x[i],b=x[i+lag];
                              num+=a*b; ea+=a*a; eb+=b*b; }
    vals[lag-minLag]=num/(Math.sqrt(ea*eb)+1e-12);
  }
  let pico=0; for(let i=0;i<vals.length;i++) if(vals[i]>pico) pico=vals[i];
  if(pico<0.6) return -1;

  const limiar=0.93*pico;
  let k=-1;
  for(let i=1;i<vals.length-1;i++){
    if(vals[i]>=limiar && vals[i]>=vals[i-1] && vals[i]>=vals[i+1]){ k=i; break; }
  }
  if(k<0){ k=0; for(let i=0;i<vals.length;i++) if(vals[i]===pico) k=i; }

  const y0=vals[Math.max(0,k-1)], y1=vals[k], y2=vals[Math.min(vals.length-1,k+1)];
  const desv=0.5*(y0-y2)/(y0-2*y1+y2+1e-12);
  return taxa/(minLag+k+desv);
}

function distanciaSemitons(f1,f2){
  let d=12*Math.log2(f1/f2);
  d=((d+6)%12+12)%12-6;      // ignora a oitava
  return d;
}

function zerarPontos(){
  acertos=0; total=0; seq=0; melhorSeq=0;
  document.getElementById('notaAtual').textContent='0';
  document.getElementById('seqAtual').textContent='0';
  document.getElementById('fimMusica').style.display='none';
}

async function ligarPontuacao(){
  const m=FILA[idx];
  if(!m || !m.temFaixas || !refVoz){
    aviso.textContent='A pontuacao precisa de uma musica com faixa de voz '
      +'separada: e dela que sai a nota de referencia. Veja a aba Ajuda.';
    mostrarAba('ajuda');
    return false;
  }
  try{
    micFluxo=await navigator.mediaDevices.getUserMedia({audio:{
      echoCancellation:true, noiseSuppression:true, autoGainControl:false}});
  }catch(e){
    aviso.textContent='Sem acesso ao microfone. Libere a permissao no navegador.';
    return false;
  }
  micNo=ctx.createMediaStreamSource(micFluxo);
  micAnal=ctx.createAnalyser(); micAnal.fftSize=2048;
  micNo.connect(micAnal);        // so analisa, nao volta para as caixas
  vozNivel=0; montarBotoesVoz(); aplicarVoz();
  zerarPontos();
  document.getElementById('placar').classList.add('on');
  aviso.textContent='Voz original desligada: e a unica forma de o microfone '
    +'ouvir voce, e nao ela. Cante junto.';
  return true;
}

function desligarPontuacao(){
  if(micFluxo) micFluxo.getTracks().forEach(f=>f.stop());
  micFluxo=null; micNo=null; micAnal=null;
  document.getElementById('placar').classList.remove('on');
}

document.getElementById('btPontos').onclick=async e=>{
  if(pontuando){ pontuando=false; desligarPontuacao();
                 e.target.classList.remove('on'); aviso.textContent=''; return; }
  criarContexto();
  if(await ligarPontuacao()){ pontuando=true; e.target.classList.add('on'); }
};

function medir(){
  if(!pontuando || !refVoz || !micAnal || !tocando) return;
  const agora=performance.now();
  if(agora-ultimaMedida < 50) return;
  ultimaMedida=agora;

  refVoz.getFloatTimeDomainData(bufRef);
  micAnal.getFloatTimeDomainData(bufMic);
  const taxa=ctx.sampleRate;
  const fRef=detectarNota(bufRef,taxa);
  if(fRef<0){ marcarAgulha(null); return; }   // instrumental: nao conta

  total++;
  const fMic=detectarNota(bufMic,taxa);
  let ponto=0, d=null;
  if(fMic>0){
    d=distanciaSemitons(fMic,fRef);
    const abs=Math.abs(d);
    ponto = abs<1 ? 1 : (abs<2 ? 0.5 : 0);
  }
  acertos+=ponto;
  if(ponto>=1){ seq++; if(seq>melhorSeq) melhorSeq=seq; } else seq=0;

  document.getElementById('notaAtual').textContent=
    total? Math.round(acertos/total*100) : 0;
  document.getElementById('seqAtual').textContent=seq;
  marcarAgulha(d);
}

function marcarAgulha(d){
  const ag=document.getElementById('agulha');
  if(d===null||d===undefined){ ag.style.background=''; ag.classList.remove('certo','perto');
                               ag.style.left='calc(50% - 2px)'; return; }
  const pos=50 + Math.max(-1,Math.min(1,d/6))*46;
  ag.style.left='calc('+pos+'% - 2px)';
  const abs=Math.abs(d);
  ag.classList.toggle('certo', abs<1);
  ag.classList.toggle('perto', abs>=1 && abs<2);
}

function fecharPontuacao(){
  if(!pontuando || total<10) return;
  const nota=Math.round(acertos/total*100);
  const fm=document.getElementById('fimMusica');
  let texto;
  if(nota>=95) texto='Nota '+nota+'. Afinacao impecavel.';
  else if(nota>=80) texto='Nota '+nota+'. Muito bem cantada.';
  else if(nota>=60) texto='Nota '+nota+'. Boa, com alguns escorregoes.';
  else if(nota>=40) texto='Nota '+nota+'. Deu para reconhecer a musica.';
  else texto='Nota '+nota+'. Foi mais entusiasmo que afinacao.';
  fm.textContent=texto+'  Maior sequencia: '+melhorSeq+'.';
  fm.style.display='block';
}

/* ============ transporte ============ */
const btPlay=document.getElementById('btPlay');
const barra=document.getElementById('barra'), preenche=document.getElementById('preenche');
const relogio=document.getElementById('relogio');
function fmt(s){ s=Math.max(0,Math.floor(s||0));
                 return Math.floor(s/60)+':'+String(s%60).padStart(2,'0'); }

async function tocar(){
  if(!canais.length){ if(FILA.length) carregar(0,true); return; }
  try{ if(ctx.state==='suspended') await ctx.resume(); }catch(e){}
  /* allSettled, nao all: com 10 faixas, uma que falhe ao carregar rejeitava
     a promessa inteira. As outras continuavam tocando e o estado ficava
     dizendo que estava parado, entao o botao travava em "Tocar". */
  const ativos = canais.filter(c=>!c.falhou);
  const r = await Promise.allSettled(ativos.map(c=>c.el.play()));
  const ok = r.filter(x=>x.status==='fulfilled').length;
  const falhas = r.length - ok;
  if(ok===0){
    tocando=false; btPlay.textContent='Tocar';
    aviso.textContent='O navegador bloqueou o audio. Clique em Tocar de novo.';
    return;
  }
  tocando=true; btPlay.textContent='Pausar';
  if(falhas) aviso.textContent=falhas+' faixa(s) nao carregaram; '
    +'o resto esta tocando.';
}
function pausar(){
  canais.forEach(c=>{ try{ c.el.pause(); }catch(e){} });
  tocando=false; btPlay.textContent='Tocar';
}
btPlay.onclick=()=>tocando?pausar():tocar();
document.getElementById('btProx').onclick=()=>carregar(idx+1,true);
document.getElementById('btAnt').onclick=()=>carregar(idx-1,true);
document.getElementById('btVolta10').onclick=()=>pular(-10);
document.getElementById('btFrente10').onclick=()=>pular(10);
document.getElementById('btInicio').onclick=()=>{ irPara(0); ultima=-1; };

function pular(seg){
  if(!mestre) return;
  irPara(mestre.currentTime + seg);
}

function irPara(t){
  if(!mestre) return;
  t=Math.max(0,Math.min(t,mestre.duration||t));
  canais.forEach(c=>{ try{c.el.currentTime=t;}catch(e){} });
  ultima=-1;
}
barra.onclick=e=>{ const r=barra.getBoundingClientRect();
  irPara(((e.clientX-r.left)/r.width)*(mestre?mestre.duration:0)); };

/* tres estados: espectro -> mapa -> oculto */
let visual=0;
const VISUAIS=[{rot:'Espectro', mapa:false, oculto:false},
               {rot:'Mapa',     mapa:true,  oculto:false},
               {rot:'Oculto',   mapa:false, oculto:true}];
function aplicarVisual(){
  const v=VISUAIS[visual];
  document.body.classList.toggle('verMapa', v.mapa);
  document.body.classList.toggle('semEspectro', v.oculto);
  document.getElementById('dica').style.display =
    (v.mapa||v.oculto) ? 'none' : '';
  document.getElementById('dicaMapa').style.display = v.mapa ? '' : 'none';
  const b=document.getElementById('btEspectro');
  b.textContent=v.rot;
  b.classList.toggle('on', !v.oculto);
  setTimeout(()=>{ redimensionar(); dimensionarMapa(); desenharMapa(); },120);
}
document.getElementById('btEspectro').onclick=()=>{
  visual=(visual+1)%VISUAIS.length; aplicarVisual();
};

document.getElementById('btCheia').onclick=e=>{
  document.body.classList.toggle('cheia');
  e.target.classList.toggle('on');
  if(document.body.classList.contains('cheia') && document.documentElement.requestFullscreen)
    document.documentElement.requestFullscreen().catch(()=>{});
  else if(document.exitFullscreen) document.exitFullscreen().catch(()=>{});
  setTimeout(redimensionar,200);
};

addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='SELECT') return;
  if(e.code==='Space'){ e.preventDefault(); tocando?pausar():tocar(); }
  if(e.code==='ArrowRight'){ e.preventDefault(); pular(10); }
  if(e.code==='ArrowLeft'){ e.preventDefault(); pular(-10); }
  if(e.key==='0') { irPara(0); ultima=-1; }
  if(e.code==='ArrowUp'){ e.preventDefault(); mudarVoz(-1); }
  if(e.code==='ArrowDown'){ e.preventDefault(); mudarVoz(1); }
  if(e.key==='n') carregar(idx+1,true);
  if(e.key==='p') carregar(idx-1,true);
  if(e.key==='f') document.getElementById('btCheia').click();
  if(e.key==='e') document.getElementById('btEspectro').click();
  if(e.key==='s' && btPartitura.style.display!=='none') btPartitura.click();
});
function mudarVoz(passo){
  let i=NIVEIS.findIndex(n=>n.v===vozNivel);
  i=Math.min(NIVEIS.length-1,Math.max(0,i+passo));
  vozNivel=NIVEIS[i].v; montarBotoesVoz(); aplicarVoz();
}

/* ============ instalar como aplicativo ============
   Em 127.0.0.1 o Chrome e o Edge oferecem a instalacao sozinhos, desde que
   haja manifesto, icones e service worker (app/pwa.py grava os tres). O
   aplicativo instalado continua precisando do festa.bat aberto. */
let pedidoInstalar=null;
const btInstalar=document.getElementById('btInstalar');
const jaInstalado=()=>matchMedia('(display-mode: standalone)').matches
                      || navigator.standalone===true;
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/sw.js').catch(()=>{});
}
addEventListener('beforeinstallprompt',e=>{
  e.preventDefault(); pedidoInstalar=e;
  if(!jaInstalado()) btInstalar.style.display='';
});
addEventListener('appinstalled',()=>{
  pedidoInstalar=null; btInstalar.style.display='none';
  aviso.textContent='App instalado. Abra o festa.bat e depois o atalho "Festa".';
  setTimeout(()=>{ aviso.textContent=''; },6000);
});
btInstalar.onclick=async ()=>{
  if(!pedidoInstalar) return;
  pedidoInstalar.prompt();
  try{ await pedidoInstalar.userChoice; }catch(e){}
  pedidoInstalar=null; btInstalar.style.display='none';
};

/* ============ atualizacao pelo GitHub ============
   O servidor compara a versao instalada (versao.json) com a ultima do GitHub.
   Se houver nova, aparece o botao; um clique baixa, confere, troca os arquivos
   do programa (com copia de seguranca) e reinicia o servidor. */
const btAtualizarApp=document.getElementById('btAtualizarApp');
const seloVersao=document.getElementById('seloVersao');
let infoVersao=null, atualizando=false;

async function checarVersao(forcar){
  try{
    const r=await fetch('/api/versao'+(forcar?'?forcar=1':''));
    infoVersao=await r.json();
  }catch(e){ return null; }
  const v=infoVersao;
  seloVersao.textContent = 'v '+(v.local ? v.local.slice(0,7) : 'desconhecida');
  seloVersao.title = v.local_data ? 'Instalada em '+v.local_data : 'Versao instalada';
  if(v.novo){
    btAtualizarApp.style.display='';
    btAtualizarApp.textContent = v.local ? 'Atualizar app' : 'Sincronizar app';
    btAtualizarApp.title = (v.mensagem?('Novidade: '+v.mensagem+'\n'):'')
      + 'Versao '+v.remoto.slice(0,7)+' no GitHub';
  } else btAtualizarApp.style.display='none';
  return v;
}

async function acompanharAtualizacao(){
  for(;;){
    await new Promise(r=>setTimeout(r,1000));
    let j;
    try{ j=await (await fetch('/api/atualizacao')).json(); }
    catch(e){ return aguardarReinicio(); }       /* servidor saiu: reiniciando */
    aviso.textContent='Atualizando: '+(j.texto||j.fase)+(j.frac?' ('+Math.round(j.frac*100)+'%)':'');
    if(j.erro){ aviso.textContent='Atualizacao falhou: '+j.erro;
                atualizando=false; checarVersao(true); return; }
    if(j.fase==='reiniciando') return aguardarReinicio();
    if(j.fase==='pronto'){ aviso.textContent='Ja esta na versao mais nova.';
                           atualizando=false; checarVersao(true); return; }
  }
}
async function aguardarReinicio(){
  aviso.textContent='Reiniciando o servidor...';
  await new Promise(r=>setTimeout(r,4000));
  for(let i=0;i<60;i++){
    try{ const r=await fetch('/api/versao',{cache:'no-store'});
         if(r.ok){ location.reload(); return; } }catch(e){}
    await new Promise(r=>setTimeout(r,1000));
  }
  aviso.textContent='O servidor nao voltou sozinho. Feche esta janela e abra o festa.bat.';
  atualizando=false;
}
btAtualizarApp.onclick=async ()=>{
  if(atualizando||!infoVersao||!infoVersao.novo) return;
  const v=infoVersao;
  if(!confirm((v.local?'Atualizar o KaraokeSep?':'Sincronizar com a ultima versao do GitHub?')
      +(v.mensagem?('\n\nNovidade: '+v.mensagem):'')
      +'\n\nOs arquivos do programa serao trocados (uma copia fica em trabalho/backup) '
      +'e o servidor reinicia. Suas musicas, modelos e o ambiente nao sao tocados.'
      +(tocando?'\n\nA musica vai parar.':''))) return;
  atualizando=true;
  try{
    const r=await fetch('/api/atualizar',{method:'POST'});
    const j=await r.json();
    if(j.erro){ aviso.textContent=j.erro; atualizando=false; return; }
  }catch(e){ aviso.textContent='Servidor nao respondeu.'; atualizando=false; return; }
  acompanharAtualizacao();
};
checarVersao(false);
setInterval(()=>{ if(!atualizando) checarVersao(false); }, 30*60*1000);

/* ============ partitura ============
   O Colab gera, por faixa, um SVG (Verovio) e um mapa "batida -> notas que
   soam". Aqui so se le: converte o tempo do audio em batida pela grade de
   batidas detectada e destaca a nota. Nao ha biblioteca de partitura no
   navegador. */
const PART={dados:null, titulo:null, stem:null, svg:null, tok:0,
            notas:new Map(), qPorId:new Map(), eventos:[], medidas:[],
            ativos:[], cursorIdx:-2, ok:false, ajuste:0, zoom:100, manualAte:0};
try{ PART.ajuste=+localStorage.getItem('partAjuste')||0;
     PART.zoom=+localStorage.getItem('partZoom')||100; }catch(e){}
const btPartitura=document.getElementById('btPartitura');
const partFolha=document.getElementById('partFolha');
const partSvg=document.getElementById('partSvg');
const partAviso=document.getElementById('partAviso');

function partDoItem(m){
  if(!m||!m.partitura) return null;
  return Object.values(m.partitura.stems).some(s=>s.tela) ? m.partitura : null;
}
function urlPart(titulo,arq,v){
  return '/'+[titulo,'partituras',arq].map(encodeURIComponent).join('/')
         +(v?'?v='+encodeURIComponent(v):'');
}
function esc(t){ return String(t).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function avisoPart(txt,ruim){ partAviso.textContent=txt||''; partAviso.className=ruim?'ruim':''; }

function prepararPartitura(m){
  const p=partDoItem(m);
  PART.dados=null; PART.stem=null; PART.svg=null; PART.ok=false;
  PART.cursorIdx=-2; PART.ativos=[]; PART.titulo=m.titulo; PART.tok++;
  btPartitura.style.display = p ? '' : 'none';
  if(!p){ fecharPartitura(false); return; }
  if(document.body.classList.contains('verPartitura')) abrirPartitura();
}

function fecharPartitura(redimensionarTela){
  document.body.classList.remove('verPartitura');
  btPartitura.classList.remove('on');
  if(redimensionarTela!==false)
    setTimeout(()=>{ redimensionar(); dimensionarMapa(); desenharMapa(); },120);
}

async function abrirPartitura(chave){
  const m=FILA[idx], p=partDoItem(m);
  if(!p) return;
  document.body.classList.add('verPartitura');
  btPartitura.classList.add('on');
  if(!PART.dados || PART.dados._titulo!==m.titulo){
    const tok=++PART.tok;
    try{
      const r=await fetch('/api/partitura?titulo='+encodeURIComponent(m.titulo));
      if(!r.ok) throw new Error('sem resposta');
      const d=await r.json(); d._titulo=m.titulo;
      if(tok!==PART.tok) return;
      PART.dados=d;
    }catch(e){ avisoPart('Nao consegui ler a partitura desta musica.',true); return; }
  }
  const chaves=Object.keys(p.stems).filter(k=>p.stems[k].tela);
  const ordem=['vocal_principal','vocal','voz_masculina','voz_feminina','baixo'];
  const alvo = (chave&&chaves.includes(chave)) ? chave
             : (PART.stem&&chaves.includes(PART.stem)) ? PART.stem
             : (ordem.find(k=>chaves.includes(k))||chaves[0]);
  mostrarStem(alvo);
}

function montarChipsPart(p){
  const cx=document.getElementById('partChips'); cx.innerHTML='';
  const ordem=['vocal_principal','vocal','voz_masculina','voz_feminina','baixo'];
  const pos=k=>{ const i=ordem.indexOf(k); return i<0?99:i; };
  Object.entries(p.stems).filter(([k,s])=>s.tela)
        .sort((a,b)=>pos(a[0])-pos(b[0])).forEach(([k,s])=>{
    const b=document.createElement('button');
    b.className='chip'+(k===PART.stem?' on':''); b.dataset.k=k;
    b.textContent=s.nome+' · '+Math.round((s.confianca||0)*100)+'%';
    b.title='Confianca media da transcricao: '+Math.round((s.confianca||0)*100)+'%';
    b.onclick=()=>mostrarStem(k);
    cx.appendChild(b);
  });
}

function montarBaixarPart(m,p,k){
  const s=p.stems[k], cx=document.getElementById('partBaixar'); cx.innerHTML='';
  const rot={pdf:'PDF',musicxml:'MusicXML',mid:'MIDI'};
  ['pdf','musicxml','mid'].forEach(t=>{
    if(!s.arquivos[t]) return;
    const a=document.createElement('a'); a.className='btsep';
    a.href=urlPart(m.titulo,s.arquivos[t],s.hash);
    a.download=m.titulo+' - '+s.nome+'.'+(t==='mid'?'mid':t);
    a.textContent=rot[t]; cx.appendChild(a);
  });
  if(Object.values(p.stems).some(x=>x.arquivos.pdf)){
    const a=document.createElement('a'); a.className='btsep';
    a.href='/api/partitura_zip?tipo=pdf&titulo='+encodeURIComponent(m.titulo);
    a.textContent='Todos os PDFs (.zip)'; cx.appendChild(a);
  }
}

function injetarSvg(texto){
  const doc=new DOMParser().parseFromString(texto,'image/svg+xml');
  const raiz=doc.documentElement;
  if(!raiz || raiz.nodeName.toLowerCase()!=='svg' || doc.querySelector('parsererror'))
    throw new Error('SVG invalido');
  /* o SVG vem do zip do Colab; ainda assim, nada executavel entra na pagina */
  doc.querySelectorAll('script,foreignObject,iframe,object,embed').forEach(n=>n.remove());
  doc.querySelectorAll('*').forEach(n=>{
    [...n.attributes].forEach(a=>{
      const nome=a.name.toLowerCase();
      if(nome.startsWith('on') ||
         ((nome==='href'||nome==='xlink:href') && /^\s*javascript:/i.test(a.value)))
        n.removeAttribute(a.name);
    });
  });
  const no=document.importNode(raiz,true);
  const w=parseFloat(no.getAttribute('width')), h=parseFloat(no.getAttribute('height'));
  if(w&&h&&!no.getAttribute('viewBox')) no.setAttribute('viewBox','0 0 '+w+' '+h);
  no.removeAttribute('width'); no.removeAttribute('height');
  no.style.width=PART.zoom+'%'; no.style.height='auto';
  partSvg.innerHTML=''; partSvg.appendChild(no);
  return no;
}

async function mostrarStem(k){
  const m=FILA[idx], p=partDoItem(m);
  if(!p||!p.stems[k]) return;
  const s=p.stems[k], tok=++PART.tok;
  PART.stem=k; PART.svg=null; PART.ok=false; PART.ativos=[]; PART.cursorIdx=-2;
  montarChipsPart(p); montarBaixarPart(m,p,k);
  document.getElementById('partInfo').textContent=
    [s.tonalidade, s.notas+' notas',
     p.bpm?('~'+Math.round(p.bpm)+' BPM'):'',
     (p.compasso||[4,4]).join('/')].filter(Boolean).join(' · ');
  avisoPart('');
  partSvg.innerHTML='<div class="partMsg">Carregando partitura...</div>';
  let texto, tm;
  try{
    const [a,b]=await Promise.all([fetch(urlPart(m.titulo,s.arquivos.svg,s.hash)),
                                   fetch(urlPart(m.titulo,s.arquivos.timemap,s.hash))]);
    if(!a.ok||!b.ok) throw new Error('arquivo ausente');
    texto=await a.text(); tm=await b.json();
  }catch(e){
    if(tok===PART.tok) partSvg.innerHTML='<div class="partMsg">Partitura indisponivel ('+esc(e.message)+').</div>';
    return;
  }
  if(tok!==PART.tok) return;
  let no;
  try{ no=injetarSvg(texto); }
  catch(e){ partSvg.innerHTML='<div class="partMsg">Partitura ilegivel ('+esc(e.message)+').</div>'; return; }

  PART.svg=no; PART.eventos=tm.eventos||[];
  PART.notas=new Map(); no.querySelectorAll('g.note').forEach(g=>PART.notas.set(g.id,g));
  PART.qPorId=new Map();
  PART.eventos.forEach(ev=>ev[1].forEach(id=>{ if(!PART.qPorId.has(id)) PART.qPorId.set(id,ev[0]); }));
  let achou=0; PART.qPorId.forEach((q,id)=>{ if(PART.notas.has(id)) achou++; });
  PART.ok = PART.qPorId.size>0 && achou/PART.qPorId.size>=0.9
            && Array.isArray(PART.dados.batidas) && PART.dados.batidas.length>=2
            && (!s.hash||!tm.hash||s.hash===tm.hash);
  marcarCompassos(k);

  const msgs=[];
  if(!PART.ok) msgs.push('O desenho e o mapa de tempo nao combinam (arquivos de execucoes '
     +'diferentes). O cursor ficou desligado; gere a partitura de novo no Colab.');
  if((s.confianca||0)<0.6) msgs.push('Transcricao aproximada: use como guia e confira de ouvido.');
  if(/librosa/i.test(p.fonte_batidas||'')) msgs.push('As batidas vieram do rastreador simples '
     +'(Beat This! indisponivel): o cursor pode se perder em musica com andamento livre.');
  if(s.aviso) msgs.push(s.aviso);
  avisoPart(msgs.join(' '), !PART.ok);
  partFolha.scrollTop=0;
}

/* compassos em que o que foi escrito nao bate com o que se ouve */
function marcarCompassos(k){
  const conf=(PART.dados.stems[k]||{}).confianca_compassos||[];
  PART.medidas=[...PART.svg.querySelectorAll('g.measure')];
  const caixas=PART.medidas.map(g=>{ try{ return g.getBBox(); }catch(e){ return null; } });
  PART.medidas.forEach((g,i)=>{
    const c=conf[i], b=caixas[i];
    if(c===null||c===undefined||!b||!b.width) return;
    if(c>=0.75) return;
    const r=document.createElementNS('http://www.w3.org/2000/svg','rect');
    r.setAttribute('x',b.x); r.setAttribute('y',b.y);
    r.setAttribute('width',b.width); r.setAttribute('height',b.height);
    r.setAttribute('class', c<0.5?'confBaixa':'confMedia');
    const t=document.createElementNS('http://www.w3.org/2000/svg','title');
    t.textContent='Confianca do compasso: '+Math.round(c*100)+'%';
    r.appendChild(t);
    g.insertBefore(r,g.firstChild);
  });
}

/* tempo do audio <-> indice (fracionario) da batida na partitura */
function posBatida(t){
  const B=PART.dados.batidas, n=B.length;
  if(t<=B[0]) return (t-B[0])/(B[1]-B[0]);
  if(t>=B[n-1]) return n-1+(t-B[n-1])/(B[n-1]-B[n-2]);
  let lo=0, hi=n-1;
  while(hi-lo>1){ const mid=(lo+hi)>>1; if(B[mid]<=t) lo=mid; else hi=mid; }
  return lo+(t-B[lo])/(B[lo+1]-B[lo]);
}
function tempoDaBatida(q){
  const B=PART.dados.batidas, n=B.length;
  const i=Math.max(0,Math.min(n-2,Math.floor(q)));
  return B[i]+(q-i)*(B[i+1]-B[i]);
}
/* o cursor mostra o que se OUVE: tira a latencia de saida e soma o ajuste */
function desvioPart(){
  const lat=(ctx&&ctx.outputLatency)||0;
  return -lat+PART.ajuste/1000;
}

function atualizarPartitura(){
  if(!PART.svg||!PART.ok||!mestre) return;
  const q=posBatida(mestre.currentTime+desvioPart())+1e-6;
  const E=PART.eventos;
  let lo=-1, hi=E.length;                /* ultimo evento com E[i][0] <= q */
  while(hi-lo>1){ const mid=(lo+hi)>>1; if(E[mid][0]<=q) lo=mid; else hi=mid; }
  if(lo===PART.cursorIdx) return;
  PART.cursorIdx=lo;
  PART.ativos.forEach(g=>g.classList.remove('tocando'));
  PART.ativos=[];
  if(lo>=0) E[lo][1].forEach(id=>{
    const g=PART.notas.get(id);
    if(g){ g.classList.add('tocando'); PART.ativos.push(g); }
  });
  if(PART.ativos.length) rolarPart(PART.ativos[0]);
}

function rolarPart(el){
  if(Date.now()<PART.manualAte) return;   /* a pessoa esta rolando a mao */
  const fr=partFolha.getBoundingClientRect(), er=el.getBoundingClientRect();
  const topo=er.top-fr.top, base=er.bottom-fr.top;
  if(topo<fr.height*0.10 || base>fr.height*0.70)
    partFolha.scrollTo({top:partFolha.scrollTop+topo-fr.height*0.28, behavior:'smooth'});
}
['wheel','touchmove','mousedown'].forEach(ev=>
  partFolha.addEventListener(ev,()=>{ PART.manualAte=Date.now()+3500; },{passive:true}));

/* a nota mais proxima do clique: acertar a cabeca de uma nota de 1 cm na
   unha e ruim; vale clicar perto dela, no mesmo sistema */
function notaPerto(x,y){
  let melhor=null, dmin=36;
  PART.notas.forEach((g,id)=>{
    if(!PART.qPorId.has(id)) return;
    const r=g.getBoundingClientRect();
    const dx=Math.max(r.left-x,0,x-r.right), dy=Math.max(r.top-y,0,y-r.bottom);
    const d=Math.hypot(dx,dy*1.4);
    if(d<dmin){ dmin=d; melhor=id; }
  });
  return melhor;
}
partFolha.addEventListener('click',ev=>{
  if(!PART.svg||!PART.ok) return;
  let q=null;
  const g=ev.target.closest?ev.target.closest('g.note'):null;
  const id=(g&&PART.qPorId.has(g.id))?g.id:notaPerto(ev.clientX,ev.clientY);
  if(id) q=PART.qPorId.get(id);
  else{
    const me=ev.target.closest?ev.target.closest('g.measure'):null;
    const i=PART.medidas.indexOf(me);
    if(i>=0) q=i*((PART.dados.compasso||[4])[0]||4);
  }
  if(q===null) return;
  PART.manualAte=0;
  irPara(Math.max(0,tempoDaBatida(q)-desvioPart()));
});

const partAjuste=document.getElementById('partAjuste'), partZoom=document.getElementById('partZoom');
partAjuste.value=PART.ajuste; partZoom.value=PART.zoom;
document.getElementById('partAjusteV').textContent=PART.ajuste+' ms';
document.getElementById('partZoomV').textContent=PART.zoom+'%';
partAjuste.oninput=()=>{
  PART.ajuste=+partAjuste.value; PART.cursorIdx=-2;
  document.getElementById('partAjusteV').textContent=PART.ajuste+' ms';
  try{ localStorage.setItem('partAjuste',PART.ajuste); }catch(e){}
};
partZoom.oninput=()=>{
  PART.zoom=+partZoom.value;
  document.getElementById('partZoomV').textContent=PART.zoom+'%';
  if(PART.svg) PART.svg.style.width=PART.zoom+'%';
  try{ localStorage.setItem('partZoom',PART.zoom); }catch(e){}
};
btPartitura.onclick=()=>{
  if(document.body.classList.contains('verPartitura')) fecharPartitura();
  else abrirPartitura();
};

/* laco de sincronia e relogio */
setInterval(()=>{
  if(!mestre) return;
  const t=mestre.currentTime, d=mestre.duration||0;
  preenche.style.width = d?(t/d*100)+'%':'0';
  relogio.textContent = fmt(t)+' / '+fmt(d);
  atualizarLetra(t);
  if(d && t>=d-0.25 && tocando){
    fecharPontuacao();
    if(idx<FILA.length-1){ carregar(idx+1,true); return; }
    pausar(); return;
  }
  /* a verdade e o elemento, nao a variavel: se algo mudar o estado por
     fora, o botao se corrige sozinho em vez de travar */
  const rodando = mestre && !mestre.paused && !mestre.ended;
  if(rodando !== tocando){
    tocando = rodando;
    btPlay.textContent = tocando ? 'Pausar' : 'Tocar';
  }
  if(!tocando) return;
  canais.forEach(c=>{ if(c.el===mestre||c.falhou) return;
    if(Math.abs(c.el.currentTime-t)>0.08){ try{c.el.currentTime=t;}catch(e){} } });
},200);

montarFila(); montarBotoesVoz(); contarPendentes();
</script>
</body>
</html>
"""
