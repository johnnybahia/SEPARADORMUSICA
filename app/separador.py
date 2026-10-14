"""Motor de separacao.

Cada passada isolada: se um modelo falhar, o resto do pipeline continua e o
stem entra no relatorio como pulado.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from . import modelos
from .util import PASTA_MODELOS, RAIZ, log, nome_seguro

# nome bruto entre parenteses -> nome final em portugues
MAPA_STEMS = {
    "vocals": "vocal", "vocal": "vocal", "lead vocals": "vocal_principal",
    "instrumental": "instrumental", "no vocals": "instrumental",
    "drums": "bateria", "bass": "baixo", "guitar": "guitarra",
    "piano": "piano", "other": "outros",
    "back vocals": "coro", "backing vocals": "coro", "backing": "coro",
    "male": "voz_masculina", "female": "voz_feminina",
    "kick": "bumbo", "snare": "caixa", "toms": "toms", "cymbals": "pratos",
    "hh": "chimbal", "hihat": "chimbal", "ride": "conducao", "crash": "crash",
}

# stems que o mixer trata como "instrumento" para o modo treino
INSTRUMENTOS = ["bateria", "baixo", "guitarra", "piano", "outros",
                "bumbo", "caixa", "toms", "pratos", "chimbal", "conducao", "crash"]


ARQUIVO_TEMPOS = RAIZ / "tempos.json"


def registrar_tempo(papel: str, modelo: str, entrada: Path,
                    segundos: float) -> None:
    """Guarda quanto custou cada passada, por minuto de audio.

    Estimativa chutada engana: os modelos da cascata (Roformer, MDX23C) sao
    de outra familia e podem custar dez vezes o htdemucs na mesma maquina.
    Medindo, a tela passa a dizer a verdade.
    """
    try:
        import json
        import soundfile as sf

        with sf.SoundFile(str(entrada)) as f:
            minutos = (len(f) / f.samplerate) / 60.0
        if minutos <= 0.05:
            return
        por_minuto = segundos / minutos

        dados = {}
        if ARQUIVO_TEMPOS.exists():
            try:
                dados = json.loads(ARQUIVO_TEMPOS.read_text(encoding="utf-8"))
            except Exception:
                dados = {}
        reg = dados.get(papel, {})
        n = reg.get("amostras", 0)
        media = reg.get("seg_por_min", por_minuto)
        # media movel: a medicao nova pesa mais que o historico antigo
        nova = (media * n + por_minuto) / (n + 1)
        dados[papel] = {"modelo": modelo, "seg_por_min": round(nova, 1),
                        "amostras": n + 1,
                        "ultima": round(por_minuto, 1)}
        ARQUIVO_TEMPOS.write_text(json.dumps(dados, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        log.info("Passada %s (%s): %.0f s para %.1f min de audio "
                 "= %.0f s por minuto", papel, modelo, segundos, minutos,
                 por_minuto)
    except Exception as e:
        log.debug("Nao registrei o tempo: %s", e)


def tempos_medidos() -> dict:
    try:
        import json
        if ARQUIVO_TEMPOS.exists():
            return json.loads(ARQUIVO_TEMPOS.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


@dataclass
class Resultado:
    pasta: Path
    stems: dict[str, Path] = field(default_factory=dict)
    passadas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    niveis: dict[str, float] = field(default_factory=dict)


def _classificar(caminho: Path) -> str:
    m = re.findall(r"\(([^)]+)\)", caminho.stem)
    bruto = (m[-1] if m else caminho.stem).strip().lower()
    bruto = re.sub(r"[_\-]+", " ", bruto).strip()
    if bruto in MAPA_STEMS:
        return MAPA_STEMS[bruto]
    for chave, valor in MAPA_STEMS.items():
        if chave in bruto:
            return valor
    return nome_seguro(bruto, 24).replace(" ", "_").lower()


def _energia_dbfs(caminho: Path) -> float:
    try:
        with sf.SoundFile(str(caminho)) as f:
            n = min(len(f), f.samplerate * 90)
            f.seek(max(0, (len(f) - n) // 2))
            x = f.read(n, dtype="float32", always_2d=True)
        if x.size == 0:
            return -120.0
        rms = float(np.sqrt(np.mean(np.square(x))))
        return 20 * np.log10(max(rms, 1e-12))
    except Exception:
        return 0.0


class Motor:
    def __init__(self, perfil, formato: str = "FLAC"):
        self.perfil = perfil
        self.formato = formato.upper()
        self._sep = None
        self._modelo_atual = None
        # arquivos gravados nesta execucao; so eles contam como conflito
        self._criados: set = set()

    def _separador(self, saida: Path):
        from audio_separator.separator import Separator
        if self._sep is None:
            kwargs = dict(
                log_level=40,
                model_file_dir=str(PASTA_MODELOS),
                output_dir=str(saida),
                output_format=self.formato,
            )
            # shifts e o multiplicador direto de tempo nos modelos Demucs:
            # a musica e processada uma vez por shift e o resultado e a
            # media. Em CPU isso e a diferenca entre 8 e 16 minutos, para
            # um ganho que quase nao se ouve.
            if not self.perfil.gpu:
                kwargs["demucs_params"] = {"shifts": 1, "overlap": 0.25,
                                           "segments_enabled": True}
            try:
                self._sep = Separator(**kwargs)
            except TypeError as e:
                log.warning("Separator recusou parametros (%s); usando o basico", e)
                for chave in ("demucs_params", "output_format"):
                    kwargs.pop(chave, None)
                self._sep = Separator(**kwargs)
        else:
            self._sep.output_dir = str(saida)
        return self._sep

    def _carregar(self, arquivo_modelo: str, saida: Path):
        sep = self._separador(saida)
        if self._modelo_atual != arquivo_modelo:
            log.info("Carregando modelo %s", arquivo_modelo)
            sep.load_model(model_filename=arquivo_modelo)
            self._modelo_atual = arquivo_modelo
        return sep

    def passada(self, entrada: Path, papel: str, saida: Path,
                mapa: dict[str, str] | None = None) -> dict[str, Path]:
        """Roda um modelo e devolve os stems ja renomeados.

        'mapa' renomeia stems da cascata para nao colidir com os da passada
        anterior (o modelo de coro devolve 'vocal' e sobrescreveria o vocal
        completo se nao fosse remapeado).

        Cada passada e cronometrada e o tempo por minuto de audio fica
        gravado em tempos.json. As estimativas mostradas na tela saem dai,
        em vez de chute: modelos diferentes custam coisas muito diferentes
        na mesma maquina.
        """
        import time as _t

        arquivo_modelo = modelos.resolver(papel, obrigatorio=False)
        if not arquivo_modelo:
            raise RuntimeError(f"sem modelo para o papel {papel}")

        bruto = saida / "_bruto"
        bruto.mkdir(parents=True, exist_ok=True)
        antes = set(bruto.glob("*"))

        sep = self._carregar(arquivo_modelo, bruto)
        inicio = _t.time()
        sep.separate(str(entrada))
        decorrido = _t.time() - inicio
        registrar_tempo(papel, arquivo_modelo, entrada, decorrido)

        novos = [p for p in bruto.glob("*") if p not in antes and p.is_file()]
        if not novos:
            raise RuntimeError(f"o modelo {arquivo_modelo} nao gerou arquivos")

        resultado: dict[str, Path] = {}
        for origem in novos:
            nome = _classificar(origem)
            if mapa:
                nome = mapa.get(nome, nome)
            destino = saida / f"{nome}{origem.suffix}"

            # Uma passada da cascata NAO pode apagar o que a anterior criou.
            # O modelo de bateria, por exemplo, devolve um stem "other" com o
            # resto do kit; classificado como "outros", ele sobrescrevia o
            # "outros" das 6 faixas e levava guitarra, teclado e sopro junto.
            # Mas o conflito so vale para o que ESTA execucao gerou: arquivo
            # de uma separacao anterior e para ser substituido, nao duplicado.
            conflito = (destino.resolve() == entrada.resolve()
                        or (destino.exists()
                            and destino.resolve() in self._criados))
            if conflito:
                raiz = entrada.stem.lower()
                nome = f"{raiz}_{nome}" if not nome.startswith(raiz) else f"{nome}_2"
                destino = saida / f"{nome}{origem.suffix}"
                log.info("Nome em conflito na cascata; gravando como %s",
                         destino.name)
            if destino.exists():
                destino.unlink()
            shutil.move(str(origem), str(destino))
            self._criados.add(destino.resolve())
            resultado[nome] = destino
        return resultado

    def processar(self, entrada: Path, saida: Path, modo: str,
                  cortar_vazios: bool = True, progresso=None,
                  proteger: tuple[str, ...] = ()) -> Resultado:
        """modo: karaoke | padrao | padrao_6 | profundo

        'proteger' lista faixas que nunca saem da mixagem, por exemplo o
        instrumento que o usuario escolheu para treinar.
        """
        saida.mkdir(parents=True, exist_ok=True)
        self._criados = set()
        res = Resultado(pasta=saida)

        def avancar(frac: float, texto: str):
            if progresso:
                try:
                    progresso(frac, desc=texto)
                except Exception:
                    pass
            log.info(texto)

        # ---------- passada principal ----------
        if modo == "karaoke":
            papel = "voz_qualidade" if self.perfil.modelo_karaoke == "qualidade" else "voz_rapido"
            avancar(0.10, "Separando voz e instrumental")
            res.stems.update(self.passada(entrada, papel, saida))
            res.passadas.append(papel)
        else:
            # O BS-Roformer-SW entrega as 6 faixas de uma vez e bate o
            # htdemucs em todas elas (baixo 14.6 contra 11.4, bateria 14.1
            # contra 10.6, "outros" 8.7 contra 2.6). Em compensacao e
            # pesado, entao so entra quando a maquina aguenta.
            forte = self.perfil.gpu or self.perfil.threads >= 8
            if modo in ("padrao_6", "profundo"):
                papel = "stems_qualidade" if forte else "stems6"
            elif self.perfil.gpu:
                papel = "stems_qualidade"
            elif forte:
                papel = "stems4"
            else:
                papel = "stems4_rapido"
            avancar(0.10, f"Separando faixas ({papel})")
            try:
                res.stems.update(self.passada(entrada, papel, saida))
            except Exception as e:
                reserva = ("stems6" if modo in ("padrao_6", "profundo")
                           else "stems4_rapido")
                if papel == reserva:
                    raise
                log.warning("%s falhou (%s); caindo para %s", papel, e, reserva)
                res.avisos.append(f"{papel} falhou, usei {reserva}")
                papel = reserva
                res.stems.update(self.passada(entrada, papel, saida))
            res.passadas.append(papel)

        # ---------- passadas em cascata ----------
        if modo == "profundo":
            if "bateria" in res.stems:
                avancar(0.55, "Dividindo a bateria")
                try:
                    res.stems.update(
                        self.passada(res.stems["bateria"], "drumsep", saida))
                    res.passadas.append("drumsep")
                except Exception as e:
                    res.avisos.append(f"divisao da bateria pulada: {e}")
                    log.warning("drumsep falhou: %s", e)
            if "vocal" in res.stems:
                avancar(0.75, "Separando voz principal e coro")
                try:
                    # o modelo devolve 'vocal' (voz principal) e 'instrumental'
                    # (o que sobra do vocal, ou seja, o coro)
                    res.stems.update(self.passada(
                        res.stems["vocal"], "backing", saida,
                        mapa={"vocal": "vocal_principal", "instrumental": "coro"}))
                    res.passadas.append("backing")
                except Exception as e:
                    res.avisos.append(f"separacao de coro pulada: {e}")
                    log.warning("backing falhou: %s", e)

        # ---------- faixas praticamente vazias ----------
        # Limiar relativo, nao absoluto: o que importa e a faixa estar muito
        # abaixo da mais forte da propria musica, nao um valor fixo em dBFS
        # que varia com a masterizacao. E nada e apagado: a faixa continua no
        # disco, so sai da lista de trabalho.
        if cortar_vazios and len(res.stems) > 2:
            niveis = {n: _energia_dbfs(p) for n, p in res.stems.items()}
            mais_forte = max(niveis.values()) if niveis else 0.0
            for nome, db in niveis.items():
                if nome in ("vocal", "instrumental", "vocal_principal"):
                    continue
                if nome in proteger:
                    continue
                if db < mais_forte - 45 or db < -65:
                    log.info("Faixa %s muito fraca (%.1f dBFS, %.1f abaixo da "
                             "mais forte): fora da mixagem", nome, db,
                             mais_forte - db)
                    res.avisos.append(
                        f"{nome} ficou {mais_forte - db:.0f} dB abaixo das outras "
                        f"({db:.0f} dBFS); o arquivo esta na pasta mas nao entrou "
                        f"nas bases")
                    res.stems.pop(nome)
            res.niveis = niveis

        bruto = saida / "_bruto"
        if bruto.exists():
            shutil.rmtree(bruto, ignore_errors=True)

        avancar(0.9, "Separacao concluida")
        return res
