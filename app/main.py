"""KaraokeSep - interface e orquestracao.

Rode com: rodar.bat
"""
from __future__ import annotations

import traceback
from pathlib import Path

import gradio as gr

from . import (canais, espectro_ui, festa, fila, hardware, letras, player,
               qualidade, treino)
from .separador import INSTRUMENTOS, Motor
from .util import (PASTA_SAIDA, abrir_pasta, listar_audios, log, mm_ss,
                   nome_seguro, nome_unico)

PERFIL = hardware.detectar()

# O Gradio 6 mudou onde o tema e informado. Detecta uma vez.
try:
    _VER_GRADIO = int(str(getattr(gr, "__version__", "5")).split(".")[0])
except Exception:
    _VER_GRADIO = 5
_TEMA_NO_LAUNCH = _VER_GRADIO >= 6

MODOS = {
    "Karaoke (2 faixas, mais rapido)": "karaoke",
    "Padrao (4 faixas: voz, bateria, baixo, outros)": "padrao",
    "Seis faixas (+ guitarra e piano, qualidade menor)": "padrao_6",
    "Profundo (pecas de bateria e coro separados)": "profundo",
}


def _modos_liberados() -> list[str]:
    livres = ["Karaoke (2 faixas, mais rapido)",
              "Padrao (4 faixas: voz, bateria, baixo, outros)"]
    if PERFIL.permite_seis:
        livres.append("Seis faixas (+ guitarra e piano, qualidade menor)")
    if PERFIL.permite_profundo:
        livres.append("Profundo (pecas de bateria e coro separados)")
    return livres


def _final(caminho: Path | None, formato: str) -> Path | None:
    """Converte uma base WAV para o formato escolhido e apaga o WAV."""
    if caminho is None or formato.upper() == "WAV":
        return caminho
    try:
        destino = caminho.with_suffix("." + formato.lower())
        treino.converter(caminho, destino, formato)
        caminho.unlink(missing_ok=True)
        return destino
    except Exception as e:
        log.warning("Nao converti %s: %s", caminho.name, e)
        return caminho


def _reunir(arquivos, pasta_texto: str) -> list[Path]:
    brutos: list[str] = []
    for f in arquivos or []:
        brutos.append(f if isinstance(f, str) else getattr(f, "name", str(f)))
    for linha in (pasta_texto or "").splitlines():
        linha = linha.strip().strip('"')
        if linha:
            brutos.append(linha)
    return listar_audios(brutos)


# --------------------------------------------------------------------------- #
#  Diagnostico
# --------------------------------------------------------------------------- #
def acao_diagnostico(arquivos, pasta_texto, progresso=gr.Progress()):
    faixas = _reunir(arquivos, pasta_texto)
    if not faixas:
        return [], "Nenhum audio encontrado. Arraste arquivos ou informe uma pasta."
    linhas, notas = [], []
    for i, f in enumerate(faixas):
        progresso((i + 1) / len(faixas), desc=f"Analisando {f.name}")
        try:
            d = qualidade.analisar(f)
            linhas.append(d.linha())
            if d.classe == "RUIM":
                notas.append(f"**{f.name}** — {'; '.join(d.notas) or 'qualidade baixa'}")
        except Exception as e:
            linhas.append([f.name, "ERRO", "-", "-", "-", "-", str(e)[:120]])
    resumo = ("Nada preocupante." if not notas else
              "Arquivos que vao render stems piores:\n\n- " + "\n- ".join(notas))
    return linhas, resumo


# --------------------------------------------------------------------------- #
#  Separacao
# --------------------------------------------------------------------------- #
def acao_separar(arquivos, pasta_texto, rotulo_modo, formato, alvo_treino,
                 atenuacao, extras, refazer, progresso=gr.Progress()):
    faixas = _reunir(arquivos, pasta_texto)
    if not faixas:
        return "Nenhum audio encontrado. Arraste arquivos ou informe uma pasta.", None

    modo = MODOS[rotulo_modo]
    extras = extras or []
    quer_letra = "Buscar letra sincronizada" in extras
    quer_player = "Gerar player HTML" in extras
    quer_coro = "Base com coro" in extras
    quer_fase = "Referencia rapida por cancelamento de fase" in extras
    quer_lados = "Exportar canais L, R, mid e side" in extras

    motor = Motor(PERFIL, formato=formato)
    hardware.impedir_suspensao()
    relato: list[str] = [PERFIL.resumo(), ""]
    ultima_pasta: Path | None = None

    try:
        for i, origem in enumerate(faixas):
            base = i / len(faixas)
            passo = 1 / len(faixas)

            def prog(frac, desc=""):
                progresso(base + frac * passo, desc=f"[{i+1}/{len(faixas)}] {desc}")

            if not refazer and fila.ja_feito(origem, modo):
                relato.append(f"**{origem.name}** — ja processado, pulando.")
                continue

            try:
                prog(0.01, "Lendo o arquivo")
                titulo, artista, album = letras.ler_tags(origem)
                pasta = nome_unico(PASTA_SAIDA / nome_seguro(
                    f"{artista} - {titulo}" if artista else titulo))
                pasta.mkdir(parents=True, exist_ok=True)
                ultima_pasta = pasta

                diag = qualidade.analisar(origem)
                entrada, info = canais.preparar_entrada(origem)

                bloco = [f"### {titulo}" + (f" — {artista}" if artista else ""),
                         f"- qualidade da fonte: **{diag.classe}** "
                         f"(corte real {diag.corte_khz:.1f} kHz)"]
                if diag.notas:
                    bloco.append("- " + "; ".join(diag.notas))

                # 5.1 real: caminho sem IA, imbativel
                if info.get("tem_51") and info.get("central_real"):
                    prog(0.05, "Extraindo o canal central do 5.1")
                    achados = canais.extrair_51(origem, pasta)
                    if achados:
                        bloco.append("- 5.1 com canal central real: karaoke extraido "
                                     "direto dos canais, sem IA")

                if quer_fase:
                    canais.cancelamento_de_fase(entrada, pasta / "referencia_fase.wav")
                    bloco.append("- referencia por cancelamento de fase gerada "
                                 "(comparacao rapida, nao e o resultado final)")

                if quer_lados:
                    canais.separar_lados(entrada, pasta / "canais")
                    bloco.append("- canais L, R, mid e side exportados")

                # separacao
                # separacao (o instrumento escolhido nunca e descartado)
                proteger = ((alvo_treino,) if alvo_treino and alvo_treino != "nenhum"
                            else ())
                res = motor.processar(entrada, pasta, modo, progresso=prog,
                                      proteger=proteger)
                bloco.append("- faixas: " +
                             (", ".join(sorted(res.stems)) or "nenhuma"))
                if res.niveis:
                    bloco.append("- niveis: " + ", ".join(
                        f"{n} {d:.0f} dBFS" for n, d in sorted(res.niveis.items())))
                for a in res.avisos:
                    bloco.append(f"- aviso: {a}")

                # mixagens
                prog(0.92, "Montando as bases")
                inst = treino.instrumental(res.stems, pasta / "base_instrumental.wav")
                if inst:
                    res.stems.setdefault("instrumental", _final(inst, formato))
                if quer_coro:
                    b = treino.instrumental(res.stems, pasta / "base_com_coro.wav",
                                            manter_coro=True)
                    _final(b, formato)

                if alvo_treino and alvo_treino != "nenhum":
                    saida_mo = treino.minus_one(
                        res.stems, alvo_treino,
                        pasta / f"treino_sem_{alvo_treino}.wav",
                        atenuacao_db=float(atenuacao))
                    if saida_mo:
                        _final(saida_mo, formato)
                        bloco.append(f"- base de treino sem **{alvo_treino}** "
                                     f"({atenuacao:g} dB)")
                    else:
                        bloco.append(f"- sem stem de {alvo_treino} nesta musica")

                # letra
                texto_lrc = None
                if quer_letra:
                    prog(0.95, "Buscando a letra")
                    achado = letras.buscar(titulo, artista, album, diag.duracao)
                    if achado and achado.tem_sincronia:
                        letras.salvar_lrc(achado, pasta / "letra.lrc")
                        texto_lrc = achado.sincronizada
                        bloco.append(f"- letra sincronizada encontrada ({achado.fonte})")
                    elif achado:
                        (pasta / "letra_sem_tempo.txt").write_text(
                            achado.simples or "", encoding="utf-8")
                        bloco.append("- so achei letra sem marcacao de tempo")
                    else:
                        bloco.append("- letra nao encontrada; renomeie como "
                                     "'Artista - Musica' e tente de novo")

                if quer_player:
                    player.gerar(pasta / "player.html", titulo, artista,
                                 res.stems, texto_lrc,
                                 alvo_treino if alvo_treino != "nenhum" else None)
                    bloco.append("- player.html gerado nesta pasta")

                bloco.append(f"- duracao: {mm_ss(diag.duracao)} · pasta: `{pasta.name}`")
                relato.append("\n".join(bloco) + "\n")
                fila.marcar(origem, modo, "ok", pasta)

            except Exception as e:
                log.error("Falha em %s: %s", origem.name, traceback.format_exc())
                relato.append(f"### {origem.name}\n- **erro:** {e}\n")
                fila.marcar(origem, modo, "erro", erro=str(e))
    finally:
        hardware.liberar_suspensao()

    if PERFIL.aviso:
        relato.append(f"\n> {PERFIL.aviso}")
    return "\n".join(relato), str(ultima_pasta) if ultima_pasta else None


def acao_transpor(arquivo, semitons):
    if not arquivo:
        return "Escolha um arquivo."
    origem = Path(arquivo if isinstance(arquivo, str) else arquivo.name)
    destino = origem.parent / f"{origem.stem}_transposto_{semitons:+g}.wav"
    try:
        treino.transpor(origem, destino, float(semitons))
        return f"Gerado: `{destino}`"
    except Exception as e:
        return f"Erro: {e}"


def acao_abrir():
    abrir_pasta(PASTA_SAIDA)
    return f"Pasta aberta: `{PASTA_SAIDA}`"


def acao_limpar_fila():
    fila.limpar()
    return "Historico limpo. O proximo lote reprocessa tudo."


# --------------------------------------------------------------------------- #
#  Interface
# --------------------------------------------------------------------------- #
def construir():
    # Gradio 6 passa o tema no launch(); versoes anteriores, no Blocks.
    bloco_kwargs = {"title": "KaraokeSep"}
    if not _TEMA_NO_LAUNCH:
        bloco_kwargs["theme"] = gr.themes.Soft()
    with gr.Blocks(**bloco_kwargs) as ui:
        gr.Markdown(f"## KaraokeSep\n{PERFIL.resumo()}"
                    + (f"\n\n> {PERFIL.aviso}" if PERFIL.aviso else ""))

        with gr.Tab("Separar"):
            with gr.Row():
                with gr.Column(scale=3):
                    entrada_arquivos = gr.Files(
                        label="Arraste as musicas aqui",
                        file_types=["audio", "video"], type="filepath")
                    entrada_pasta = gr.Textbox(
                        label="Ou cole caminhos de arquivo ou pasta, um por linha",
                        placeholder=r"C:\Users\voce\Music\album", lines=2)
                with gr.Column(scale=2):
                    modo = gr.Radio(_modos_liberados(), value=_modos_liberados()[0],
                                    label="Modo")
                    formato = gr.Radio(["FLAC", "MP3", "WAV"], value="MP3",
                                       label="Formato das faixas",
                                       info="MP3 320 toca em qualquer lugar. "
                                            "FLAC guarda sem perda.")
            with gr.Row():
                alvo = gr.Dropdown(["nenhum"] + INSTRUMENTOS, value="nenhum",
                                   label="Instrumento que voce vai tocar",
                                   info="Gera uma base com esse stem abaixado.")
                atenuacao = gr.Slider(-40, 0, value=-15, step=1,
                                      label="Quanto abaixar esse stem (dB)",
                                      info="-15 dB mantem a referencia sem expor "
                                           "o vazamento. -40 e mudo total.")
            extras = gr.CheckboxGroup(
                ["Buscar letra sincronizada", "Gerar player HTML", "Base com coro",
                 "Referencia rapida por cancelamento de fase",
                 "Exportar canais L, R, mid e side"],
                value=["Buscar letra sincronizada", "Gerar player HTML"],
                label="Extras")
            refazer = gr.Checkbox(False, label="Refazer musicas ja processadas")

            bt = gr.Button("Separar", variant="primary")
            saida_texto = gr.Markdown()
            saida_pasta = gr.Textbox(label="Ultima pasta gerada", interactive=False)
            bt.click(acao_separar,
                     [entrada_arquivos, entrada_pasta, modo, formato, alvo,
                      atenuacao, extras, refazer],
                     [saida_texto, saida_pasta])

        with gr.Tab("Diagnostico"):
            gr.Markdown("Mede o corte real de banda pelo espectro. "
                        "Denuncia MP3 de 128 kbps reetiquetado como 320.")
            with gr.Row():
                diag_arquivos = gr.Files(label="Arquivos", file_types=["audio", "video"],
                                         type="filepath")
                diag_pasta = gr.Textbox(label="Ou caminhos, um por linha", lines=3)
            bt_diag = gr.Button("Analisar")
            tabela = gr.Dataframe(headers=qualidade.COLUNAS, wrap=True)
            resumo = gr.Markdown()
            bt_diag.click(acao_diagnostico, [diag_arquivos, diag_pasta],
                          [tabela, resumo])

        with gr.Tab("Editor espectral"):
            gr.Markdown(
                "Mesma ideia do Roland R-MIX, com uma vantagem: os stems ja "
                "existem, entao da para tirar so a parcela de um instrumento "
                "num ponto do espectro em vez de apagar tudo que estiver ali.\n\n"
                "**Comece pela limpeza automatica** — ela nao exige selecionar "
                "nada e costuma ser o maior ganho."
            )
            with gr.Row():
                e_musica = gr.Dropdown(espectro_ui.listar_musicas(),
                                       label="Musica", scale=3)
                e_stem = gr.Dropdown([], label="Faixa", scale=2)
                e_atualiza = gr.Button("Recarregar lista", scale=1)

            with gr.Accordion("Limpeza automatica de vazamento", open=True):
                gr.Markdown(
                    "Onde outro instrumento domina o ponto do espectro, o que "
                    "sobra nesta faixa e residuo. Atenua isso sozinho.")
                with gr.Row():
                    e_forca = gr.Slider(0.5, 3.0, value=1.5, step=0.1,
                                        label="Forca",
                                        info="Acima de 2 o som fica fino.")
                    e_piso = gr.Slider(-40, -6, value=-18, step=1,
                                       label="Piso (dB)",
                                       info="O quanto o residuo pode cair.")
                e_bt_limpar = gr.Button("Limpar vazamento", variant="primary")

            with gr.Accordion("Selecao manual", open=False):
                with gr.Row():
                    e_ini = gr.Slider(0, 600, value=30, step=1,
                                      label="Inicio da janela (s)")
                    e_dur = gr.Slider(2, 30, value=12, step=1,
                                      label="Tamanho da janela (s)")
                e_img = gr.Image(label="Espectrograma", type="pil", height=460)
                e_info = gr.Markdown()

                e_tipo = gr.Radio(["Retangulo (tempo x frequencia)",
                                   "Harmonicos de uma nota"],
                                  value="Retangulo (tempo x frequencia)",
                                  label="Tipo de selecao")
                with gr.Row():
                    e_t0 = gr.Slider(0, 30, value=0, step=0.1,
                                     label="Inicio dentro da janela (s)")
                    e_t1 = gr.Slider(0, 30, value=12, step=0.1,
                                     label="Fim dentro da janela (s)")
                with gr.Row():
                    e_f0 = gr.Slider(40, 16000, value=200, step=10,
                                     label="Frequencia minima (Hz)")
                    e_f1 = gr.Slider(40, 16000, value=2000, step=10,
                                     label="Frequencia maxima (Hz)")
                with gr.Row():
                    e_fund = gr.Slider(40, 2000, value=220, step=1,
                                       label="Fundamental (Hz)",
                                       info="So para selecao por harmonicos.")
                    e_nh = gr.Slider(1, 24, value=10, step=1,
                                     label="Quantos harmonicos")
                with gr.Row():
                    e_op = gr.Radio(["Atenuar", "Isolar", "Realcar"],
                                    value="Atenuar", label="Operacao")
                    e_ganho = gr.Slider(-60, 12, value=-15, step=1,
                                        label="Ganho na selecao (dB)")
                    e_posse = gr.Checkbox(
                        True, label="Usar razao de posse",
                        info="Desmarque para apagar a regiao inteira, "
                             "como o R-MIX fazia.")
                with gr.Row():
                    e_bt_ver = gr.Button("Ver selecao")
                    e_bt_previa = gr.Button("Ouvir previa da janela")
                    e_bt_export = gr.Button("Aplicar na musica inteira",
                                            variant="primary")
                with gr.Row():
                    e_antes = gr.Audio(label="Antes")
                    e_depois = gr.Audio(label="Depois")

            e_saida = gr.Markdown()
            e_arquivo = gr.Audio(label="Resultado", interactive=False)

            def _recarregar():
                return gr.update(choices=espectro_ui.listar_musicas())

            def _stems(musica):
                nomes = sorted(espectro_ui.stems_da_musica(musica))
                escolha = next((n for n in nomes if n not in ("instrumental",)), None)
                return gr.update(choices=nomes, value=escolha)

            e_atualiza.click(_recarregar, None, e_musica)

            sel = [e_tipo, e_t0, e_t1, e_f0, e_f1, e_fund, e_nh]
            entradas_ver = [e_musica, e_stem, e_ini, e_dur] + sel

            def _ligar(componente, fn, entradas, saidas):
                """Usa .release em sliders quando existir, para nao disparar
                a cada pixel arrastado."""
                evento = getattr(componente, "release", None) or componente.change
                evento(fn, entradas, saidas)

            # o espectrograma se redesenha sozinho: escolher a musica ja mostra
            e_musica.change(_stems, e_musica, e_stem).then(
                espectro_ui.ver, entradas_ver, [e_img, e_info])
            e_stem.change(espectro_ui.ver, entradas_ver, [e_img, e_info])
            for c in (e_ini, e_dur, e_t0, e_t1, e_f0, e_f1, e_fund, e_nh):
                _ligar(c, espectro_ui.ver, entradas_ver, [e_img, e_info])
            e_tipo.change(espectro_ui.ver, entradas_ver, [e_img, e_info])

            e_bt_ver.click(espectro_ui.ver, entradas_ver, [e_img, e_info])
            e_bt_previa.click(espectro_ui.previa,
                              entradas_ver + [e_op, e_ganho, e_posse],
                              [e_antes, e_depois, e_info])
            e_bt_export.click(
                lambda m, s, tp, i, d, a, b, f0, f1, fu, nh, op, g, up,
                progress=gr.Progress(): espectro_ui.exportar(
                    m, s, tp, i, d, a, b, f0, f1, fu, nh, op, g, up, progress),
                [e_musica, e_stem, e_tipo, e_ini, e_dur, e_t0, e_t1, e_f0, e_f1,
                 e_fund, e_nh, e_op, e_ganho, e_posse],
                [e_saida, e_arquivo])
            e_bt_limpar.click(
                lambda m, s, f, p, progress=gr.Progress():
                    espectro_ui.limpar(m, s, f, p, progress),
                [e_musica, e_stem, e_forca, e_piso], [e_saida, e_arquivo])

        with gr.Tab("Modo festa"):
            gr.Markdown(
                "Pagina propria que toca a fila com processamento ao vivo no "
                "navegador. Botao de voz em 4 niveis, mixer por instrumento, "
                "espectro e filtro de frequencia em tempo real.\n\n"
                "**Musica com faixas separadas** usa os stems: qualidade "
                "maxima e CPU parada. **Musica sem faixas** cai no "
                "cancelamento de centro ao vivo, aplicado so na banda da voz "
                "para nao levar o grave junto — funciona em gravacao com voz "
                "seca no centro, falha em producao com voz alargada."
            )
            f_bt = gr.Button("Gerar a pagina da festa", variant="primary")
            f_abrir_nav = gr.Button("Abrir a festa no navegador", variant="primary")
            f_saida = gr.Markdown()
            f_tabela = gr.Dataframe(
                headers=["Musica", "Motor", "Faixas", "Letra"], wrap=True)
            f_abrir = gr.Button("Abrir a pasta da festa")

            def _gerar_festa():
                try:
                    destino, lista = festa.gerar()
                except Exception as e:
                    log.error("Falha ao gerar a festa: %s", e, exc_info=True)
                    return f"Erro: {e}", []
                if not lista:
                    return ("Nenhuma musica encontrada em `saida`. "
                            "Separe alguma antes."), []
                linhas = [[
                    m["titulo"],
                    "faixas separadas" if m["temFaixas"] else "cancelamento ao vivo",
                    str(len(m["canais"])) if m["temFaixas"] else "-",
                    "sim" if m["letra"] else "nao",
                ] for m in lista]
                com = sum(1 for m in lista if m["temFaixas"])
                try:
                    url = festa.endereco()
                    linha_url = (f"Endereco: **{url}**\n\n"
                                 f"Use o botao acima ou o `festa.bat` na pasta "
                                 f"do projeto. **Nao abra o arquivo festa.html "
                                 f"com dois cliques**: aberto direto do disco, "
                                 f"o navegador silencia o processamento de "
                                 f"audio sem avisar.")
                except Exception as e:
                    linha_url = f"Nao consegui subir o servidor local: {e}"
                return (f"Pronto: {len(lista)} musicas na fila, {com} com "
                        f"faixas separadas.\n\n{linha_url}\n\n"
                        f"Atalhos: espaco toca e pausa, setas para cima e para "
                        f"baixo mudam o nivel da voz, `n` e `p` trocam de "
                        f"musica, `f` entra em tela cheia.\n\n"
                        f"Lembre: **o microfone nao pode passar pelo PC** — a "
                        f"latencia do Windows inviabiliza cantar. Ligue o "
                        f"microfone direto na caixa ou numa mesa.",
                        linhas)

            def _abrir_festa():
                import webbrowser
                try:
                    if not (PASTA_SAIDA / "festa.html").exists():
                        festa.gerar()
                    url = festa.endereco()
                    webbrowser.open(url)
                    return f"Abrindo **{url}**"
                except Exception as e:
                    log.error("Falha ao abrir a festa: %s", e, exc_info=True)
                    return f"Erro ao abrir: {e}"

            f_bt.click(_gerar_festa, None, [f_saida, f_tabela])
            f_abrir_nav.click(_abrir_festa, None, f_saida)
            f_abrir.click(acao_abrir, None, f_saida)

        with gr.Tab("Ferramentas"):
            gr.Markdown("### Reforcar o volume de uma faixa")
            gr.Markdown(
                "Uma faixa de baixo sai baixa porque ela **e** baixa dentro da "
                "mistura: o modelo nao mudou o nivel, so tirou o resto. Para "
                "ouvir sozinha, precisa de ganho.\n\n"
                "O arquivo reforcado e novo; o original continua intacto. "
                "**Nao troque o original pelo reforcado** se voce usa o mixer: "
                "levantar cada faixa em separado destroi o equilibrio entre elas.")
            with gr.Row():
                v_musica = gr.Dropdown(espectro_ui.listar_musicas(),
                                       label="Musica", scale=3)
                v_stem = gr.Dropdown([], label="Faixa", scale=2)
                v_recarrega = gr.Button("Recarregar", scale=1)
            v_medida = gr.Markdown()
            with gr.Row():
                v_modo = gr.Radio(
                    ["Pico (o maximo sem distorcer)",
                     "Volume parelho (RMS alvo)",
                     "Ganho manual"],
                    value="Pico (o maximo sem distorcer)", label="Modo")
                v_alvo = gr.Slider(-30, -1, value=-14, step=1,
                                   label="RMS alvo (dBFS)",
                                   info="So no modo parelho. -14 e o padrao "
                                        "de streaming.")
                v_manual = gr.Slider(-20, 30, value=8, step=1,
                                     label="Ganho manual (dB)")
            v_bt = gr.Button("Gerar faixa reforcada", variant="primary")
            v_saida = gr.Markdown()
            v_audio = gr.Audio(label="Resultado", interactive=False)

            def _v_stems(musica):
                nomes = sorted(espectro_ui.stems_da_musica(musica))
                return gr.update(choices=nomes, value=nomes[0] if nomes else None)

            def _v_medir(musica, stem):
                caminho = espectro_ui.stems_da_musica(musica).get(stem)
                if not caminho:
                    return ""
                try:
                    m = treino.medir(caminho)
                    folga = -1.0 - m["pico_db"]
                    return (f"`{stem}` — pico **{m['pico_db']:.1f} dBFS**, "
                            f"RMS **{m['rms_db']:.1f} dBFS**. "
                            f"Da para levantar **{folga:+.1f} dB** sem distorcer nada.")
                except Exception as e:
                    return f"Nao consegui medir: {e}"

            def _v_reforcar(musica, stem, modo, alvo, manual):
                caminho = espectro_ui.stems_da_musica(musica).get(stem)
                if not caminho:
                    return "Escolha uma faixa.", None
                curto = ("pico" if modo.startswith("Pico")
                         else "rms" if modo.startswith("Volume") else "manual")
                sufixo = {"pico": "forte", "rms": "parelho", "manual": "ganho"}[curto]
                destino = nome_unico(caminho.parent / f"{stem}_{sufixo}.wav")
                try:
                    _, relato = treino.reforcar(caminho, destino, curto,
                                                float(alvo), float(manual))
                    return f"Gerado: `{destino.name}`\n\n{relato}", str(destino)
                except Exception as e:
                    log.error("Falha ao reforcar: %s", e)
                    return f"Erro: {e}", None

            v_recarrega.click(lambda: gr.update(choices=espectro_ui.listar_musicas()),
                              None, v_musica)
            v_musica.change(_v_stems, v_musica, v_stem)
            v_stem.change(_v_medir, [v_musica, v_stem], v_medida)
            v_bt.click(_v_reforcar, [v_musica, v_stem, v_modo, v_alvo, v_manual],
                       [v_saida, v_audio])

            gr.Markdown("### Mudar o tom sem mudar a velocidade")
            arq_t = gr.File(label="Arquivo", type="filepath")
            semi = gr.Slider(-5, 5, value=0, step=1, label="Semitons")
            bt_t = gr.Button("Transpor")
            res_t = gr.Markdown()
            bt_t.click(acao_transpor, [arq_t, semi], res_t)

            gr.Markdown("### Pastas e historico")
            with gr.Row():
                bt_abrir = gr.Button("Abrir a pasta de saida")
                bt_fila = gr.Button("Limpar o historico da fila")
            res_f = gr.Markdown()
            bt_abrir.click(acao_abrir, None, res_f)
            bt_fila.click(acao_limpar_fila, None, res_f)

        with gr.Tab("Ajuda"):
            gr.Markdown(f"""
### O que cada modo faz

**Karaoke** roda um modelo so e devolve voz e instrumental. E o mais rapido e o
melhor resultado quando voce so quer cantar.

**Padrao** devolve voz, bateria, baixo e outros. E o de maior qualidade para
treinar baixo ou bateria.

**Seis faixas** acrescenta guitarra e piano, mas a qualidade do stem "outros"
cai bastante. Use so quando precisar mesmo desses dois.

**Profundo** parte a bateria em pecas e separa voz principal de coro.

### Treinar instrumento

Marque o instrumento que voce vai tocar. A base sai com esse stem em -15 dB em
vez de mudo: o residuo serve de referencia de tempo e o seu instrumento cobre o
vazamento do modelo. Mute total so parece melhor ate voce ouvir o fantasma.

Baixo e bateria separam muito bem. Guitarra da para usar. Piano decepciona.

### Letra

A busca usa o LRCLIB, que casa por titulo, artista e duracao. Se o arquivo tiver
tags erradas ou nome como track01.mp3, nao acha nada. Renomeie para
"Artista - Musica" e tente de novo. Voce tambem pode colocar um arquivo
letra.lrc na pasta da musica na mao.

### Onde ficam os arquivos

`{PASTA_SAIDA}`

Uma pasta por musica, com as faixas, as bases e o player.html.

### Passando para outro PC

Copie a pasta do projeto **sem** o venv e rode instalar.bat la. A pasta
`modelos` pode ir junto: evita baixar os pesos de novo.
""")
    return ui


def main():
    log.info("Perfil: %s", PERFIL.resumo())
    ui = construir()
    # O Gradio 6 move 'theme' para o launch() e troca 'show_api' por
    # 'footer_links'. Tenta o jeito novo e cai para o antigo.
    tema = getattr(gr.themes, "Soft", None)
    try:
        ui.queue().launch(server_name="127.0.0.1", inbrowser=True,
                          theme=tema() if tema else None,
                          footer_links=["gradio", "settings"], quiet=False)
    except TypeError:
        ui.queue().launch(server_name="127.0.0.1", inbrowser=True,
                          show_api=False, quiet=False)


if __name__ == "__main__":
    main()
