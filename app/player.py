"""Gera um player HTML autonomo dentro da pasta da musica.

Funciona offline: nenhuma fonte, script ou folha de estilo externa.
Os stems ficam em elementos <audio> separados, sincronizados por correcao
periodica de deriva, o que preserva a mudanca de andamento sem alterar o tom.
"""
from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITULO__</title>
<style>
  :root{
    --fundo:#14171b; --painel:#1c2127; --linha:#2b323a;
    --texto:#dae0e6; --fraco:#7b8794; --ambar:#e8a33d; --verde:#4fb3a4;
    --alerta:#d9705c;
    --fonte: "Segoe UI", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{
    background:var(--fundo); color:var(--texto); font-family:var(--fonte);
    display:flex; flex-direction:column; overflow:hidden;
  }
  header{
    padding:14px 20px 12px; border-bottom:1px solid var(--linha);
    display:flex; align-items:baseline; gap:14px; flex-wrap:wrap;
  }
  header h1{font-size:17px; font-weight:600; margin:0; letter-spacing:.2px}
  header span{color:var(--fraco); font-size:13px}
  main{flex:1; display:flex; min-height:0}

  /* ---- mixer ---- */
  #mixer{
    width:290px; border-right:1px solid var(--linha); padding:16px 14px;
    overflow-y:auto; background:var(--painel); flex-shrink:0;
  }
  #mixer h2{font-size:12px; font-weight:600; color:var(--fraco); margin:0 0 14px}
  .canal{margin-bottom:15px}
  .canal .topo{display:flex; justify-content:space-between; align-items:center; gap:8px}
  .canal .nome{font-size:13.5px; text-transform:capitalize}
  .canal .db{font-size:11.5px; color:var(--fraco); font-variant-numeric:tabular-nums; min-width:46px; text-align:right}
  .canal input[type=range]{width:100%; margin:7px 0 0; accent-color:var(--verde)}
  .canal.treino .nome{color:var(--ambar)}
  .canal.treino input[type=range]{accent-color:var(--ambar)}
  .canal.mudo{opacity:.4}
  .mini{
    background:none; border:1px solid var(--linha); color:var(--fraco);
    font:inherit; font-size:11px; padding:2px 7px; border-radius:3px; cursor:pointer;
  }
  .mini:hover{border-color:var(--fraco); color:var(--texto)}
  .mini.on{border-color:var(--ambar); color:var(--ambar)}

  /* ---- letra ---- */
  #letra{flex:1; overflow-y:auto; padding:8vh 6vw; scroll-behavior:smooth; min-width:0}
  #letra p{
    margin:0 0 .65em; font-size:22px; line-height:1.5; color:#59636e;
    transition:color .18s ease; cursor:pointer; max-width:34ch;
  }
  #letra p.passou{color:#6f7a85}
  #letra p.atual{color:var(--ambar); font-size:30px; font-weight:600}
  #vazio{color:var(--fraco); font-size:15px; line-height:1.7; max-width:46ch}

  /* ---- transporte ---- */
  footer{
    border-top:1px solid var(--linha); background:var(--painel);
    padding:12px 20px 14px; display:flex; flex-direction:column; gap:10px;
  }
  .barra{position:relative; height:6px; background:#0f1215; border-radius:3px; cursor:pointer}
  .barra .preenche{position:absolute; inset:0 auto 0 0; background:var(--verde); border-radius:3px; width:0}
  .barra .marca{position:absolute; top:-4px; width:2px; height:14px; background:var(--ambar)}
  .controles{display:flex; align-items:center; gap:14px; flex-wrap:wrap}
  button.play{
    background:var(--verde); color:#10130f; border:0; font:inherit; font-weight:600;
    padding:9px 22px; border-radius:4px; cursor:pointer; min-width:96px;
  }
  button.play:hover{filter:brightness(1.08)}
  .tempo{font-variant-numeric:tabular-nums; color:var(--fraco); font-size:13px; min-width:96px}
  .grupo{display:flex; align-items:center; gap:7px; font-size:12.5px; color:var(--fraco)}
  .grupo input[type=range]{width:124px; accent-color:var(--fraco)}
  .valor{color:var(--texto); font-variant-numeric:tabular-nums; min-width:44px}
  #aviso{color:var(--alerta); font-size:12.5px; display:none}
  :focus-visible{outline:2px solid var(--ambar); outline-offset:2px}
  @media (prefers-reduced-motion: reduce){ *{transition:none!important; scroll-behavior:auto!important} }
  @media (max-width:820px){
    main{flex-direction:column}
    #mixer{width:100%; max-height:38vh; border-right:0; border-bottom:1px solid var(--linha)}
    #letra{padding:5vh 7vw}
    #letra p{font-size:19px} #letra p.atual{font-size:25px}
  }
</style>
</head>
<body>
<header>
  <h1>__TITULO__</h1>
  <span>__ARTISTA__</span>
</header>

<main>
  <section id="mixer">
    <h2>Mixagem</h2>
    <div id="canais"></div>
  </section>
  <section id="letra"><div id="vazio"></div></section>
</main>

<footer>
  <div class="barra" id="barra">
    <div class="preenche" id="preenche"></div>
  </div>
  <div class="controles">
    <button class="play" id="btPlay">Tocar</button>
    <span class="tempo" id="tempo">0:00 / 0:00</span>
    <span class="grupo">
      Andamento
      <input type="range" id="veloc" min="50" max="120" step="5" value="100">
      <span class="valor" id="velocV">100%</span>
    </span>
    <span class="grupo">
      <button class="mini" id="btA">Marcar A</button>
      <button class="mini" id="btB">Marcar B</button>
      <button class="mini" id="btLoop">Repetir A-B</button>
      <button class="mini" id="btLimpa">Limpar</button>
    </span>
    <span class="grupo">
      <button class="mini" id="btVoltar">-5s</button>
      <button class="mini" id="btFrente">+5s</button>
    </span>
  </div>
  <div id="aviso"></div>
</footer>

<script>
const STEMS = __STEMS__;
const LETRA = __LETRA__;

const els = [];
let mestre = null, tocando = false, pontoA = null, pontoB = null, loop = false;
const aviso = document.getElementById('aviso');

function mostrarAviso(t){ aviso.textContent = t; aviso.style.display = 'block'; }

/* ---------- monta os canais ---------- */
const caixaCanais = document.getElementById('canais');
STEMS.forEach((s, i) => {
  const a = new Audio(encodeURI(s.arquivo));
  a.preload = 'auto';
  a.volume = 1;
  if ('preservesPitch' in a) a.preservesPitch = true;
  a.mozPreservesPitch = true; a.webkitPreservesPitch = true;
  a.addEventListener('error', () => mostrarAviso(
    'Nao consegui carregar ' + s.arquivo + '. Se os stems estao em FLAC, '
    + 'gere de novo escolhendo MP3 no programa.'));
  els.push({el:a, dados:s});

  const div = document.createElement('div');
  div.className = 'canal';
  div.innerHTML =
    '<div class="topo"><span class="nome">' + s.rotulo + '</span>'
    + '<span><button class="mini" data-solo="' + i + '">Solo</button> '
    + '<span class="db" id="db' + i + '">0 dB</span></span></div>'
    + '<input type="range" min="-60" max="18" step="1" value="0" data-vol="' + i + '">';
  caixaCanais.appendChild(div);
  div.dataset.idx = i;
});

/* ---------- ganho ----------
   O elemento <audio> so aceita volume de 0 a 1, entao nao da para
   passar de 0 dB por ele. A cadeia Web Audio resolve: cada faixa vai
   para um GainNode proprio, que aceita ganho acima de 1. O elemento
   continua cuidando do andamento sem alterar o tom. */
let ctx = null;
const ganhos = [];

function montarCadeia(){
  if (ctx) return true;
  if (location.protocol === 'file:') {
    /* Aberto direto do disco: o navegador trata cada arquivo como origem
       isolada e o Web Audio devolveria silencio. Segue sem cadeia; o ganho
       fica limitado a 0 dB, que e o teto do elemento <audio>. */
    return false;
  }
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    ctx = new AC();
    els.forEach((o, i) => {
      const src = ctx.createMediaElementSource(o.el);
      const g = ctx.createGain();
      g.gain.value = o.el.volume;
      src.connect(g); g.connect(ctx.destination);
      ganhos[i] = g;
      o.el.volume = 1;
    });
    return true;
  } catch(e) {
    console.warn('Web Audio indisponivel, ganho limitado a 0 dB', e);
    ctx = null;
    return false;
  }
}

function aplicarGanho(i, db){
  const linear = db <= -60 ? 0 : Math.pow(10, db/20);
  if (ganhos[i]) ganhos[i].gain.value = linear;
  else els[i].el.volume = Math.min(1, linear);
}

if (location.protocol === 'file:') {
  mostrarAviso('Aberto direto do disco: os faders funcionam ate 0 dB. '
    + 'Para reforcar acima disso, use o modo festa, que roda por servidor local.');
}

document.querySelectorAll('[data-vol]').forEach(sl => {
  sl.addEventListener('input', () => {
    const i = +sl.dataset.vol, db = +sl.value;
    aplicarGanho(i, db);
    document.getElementById('db'+i).textContent = db <= -60 ? 'mudo' : db + ' dB';
    sl.closest('.canal').classList.toggle('mudo', db <= -60);
  });
});
document.querySelectorAll('[data-solo]').forEach(bt => {
  bt.addEventListener('click', () => {
    const i = +bt.dataset.solo, ligar = !bt.classList.contains('on');
    document.querySelectorAll('[data-solo]').forEach(o => o.classList.remove('on'));
    document.querySelectorAll('[data-vol]').forEach(sl => {
      const j = +sl.dataset.vol;
      const alvo = ligar ? (j === i ? +sl.value : -60) : +sl.value;
      aplicarGanho(j, alvo);
      sl.closest('.canal').classList.toggle('mudo', ligar && j !== i);
    });
    if (ligar) bt.classList.add('on');
  });
});

/* destaca o canal marcado como alvo de treino */
STEMS.forEach((s,i) => {
  if (s.treino) caixaCanais.children[i].classList.add('treino');
});

/* ---------- letra ---------- */
const caixaLetra = document.getElementById('letra');
let linhas = [];
if (LETRA.length) {
  caixaLetra.innerHTML = '';
  LETRA.forEach(l => {
    const p = document.createElement('p');
    p.textContent = l.texto || '\u00a0';
    p.addEventListener('click', () => irPara(l.t));
    caixaLetra.appendChild(p);
    linhas.push({t:l.t, el:p});
  });
} else {
  document.getElementById('vazio').textContent =
    'Sem letra sincronizada para esta musica. Coloque um arquivo .lrc com o mesmo '
    + 'nome nesta pasta e recarregue a pagina, ou use o mixer e o loop A-B para treinar.';
}

let ultimaLinha = -1;
function atualizarLetra(t){
  if (!linhas.length) return;
  let idx = -1;
  for (let i=0; i<linhas.length; i++){ if (linhas[i].t <= t + 0.15) idx = i; else break; }
  if (idx === ultimaLinha) return;
  linhas.forEach((l,i) => {
    l.el.classList.toggle('atual', i === idx);
    l.el.classList.toggle('passou', i < idx);
  });
  if (idx >= 0) {
    const alvo = linhas[idx].el;
    caixaLetra.scrollTop = alvo.offsetTop - caixaLetra.clientHeight * 0.38;
  }
  ultimaLinha = idx;
}

/* ---------- transporte ---------- */
const btPlay = document.getElementById('btPlay');
const barra = document.getElementById('barra');
const preenche = document.getElementById('preenche');
const rotuloTempo = document.getElementById('tempo');

function fmt(s){ s = Math.max(0, Math.floor(s||0)); return Math.floor(s/60)+':'+String(s%60).padStart(2,'0'); }

function escolherMestre(){
  mestre = els.reduce((a,b) => (b.el.duration||0) > (a.el.duration||0) ? b : a, els[0]).el;
}
els.forEach(o => o.el.addEventListener('loadedmetadata', escolherMestre));
escolherMestre();

async function tocar(){
  try {
    montarCadeia();
    if (ctx && ctx.state === 'suspended') await ctx.resume();
    await Promise.all(els.map(o => o.el.play()));
    tocando = true; btPlay.textContent = 'Pausar';
  } catch(e) {
    mostrarAviso('O navegador bloqueou a reproducao. Clique em Tocar de novo.');
  }
}
function pausar(){ els.forEach(o => o.el.pause()); tocando = false; btPlay.textContent = 'Tocar'; }
btPlay.addEventListener('click', () => tocando ? pausar() : tocar());

function irPara(t){
  t = Math.max(0, Math.min(t, mestre.duration || t));
  els.forEach(o => { try { o.el.currentTime = t; } catch(e){} });
  ultimaLinha = -1;
}

barra.addEventListener('click', e => {
  const r = barra.getBoundingClientRect();
  irPara(((e.clientX - r.left) / r.width) * (mestre.duration || 0));
});

document.getElementById('btVoltar').addEventListener('click', () => irPara(mestre.currentTime - 5));
document.getElementById('btFrente').addEventListener('click', () => irPara(mestre.currentTime + 5));

const veloc = document.getElementById('veloc'), velocV = document.getElementById('velocV');
veloc.addEventListener('input', () => {
  const r = +veloc.value / 100;
  velocV.textContent = veloc.value + '%';
  els.forEach(o => { o.el.playbackRate = r; if ('preservesPitch' in o.el) o.el.preservesPitch = true; });
});

function marcador(t, id){
  document.getElementById(id)?.remove();
  const d = document.createElement('div');
  d.className = 'marca'; d.id = id;
  d.style.left = ((t / (mestre.duration||1)) * 100) + '%';
  barra.appendChild(d);
}
document.getElementById('btA').addEventListener('click', () => { pontoA = mestre.currentTime; marcador(pontoA, 'mA'); });
document.getElementById('btB').addEventListener('click', () => { pontoB = mestre.currentTime; marcador(pontoB, 'mB'); });
document.getElementById('btLoop').addEventListener('click', e => {
  loop = !loop; e.target.classList.toggle('on', loop);
  if (loop && pontoA !== null) irPara(pontoA);
});
document.getElementById('btLimpa').addEventListener('click', e => {
  pontoA = pontoB = null; loop = false;
  document.getElementById('btLoop').classList.remove('on');
  document.getElementById('mA')?.remove(); document.getElementById('mB')?.remove();
});

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if (e.code === 'Space'){ e.preventDefault(); tocando ? pausar() : tocar(); }
  if (e.code === 'ArrowLeft') irPara(mestre.currentTime - 5);
  if (e.code === 'ArrowRight') irPara(mestre.currentTime + 5);
});

/* ---------- laco de sincronia ---------- */
setInterval(() => {
  if (!mestre) return;
  const t = mestre.currentTime, dur = mestre.duration || 0;
  preenche.style.width = dur ? (t/dur*100)+'%' : '0';
  rotuloTempo.textContent = fmt(t) + ' / ' + fmt(dur);
  atualizarLetra(t);
  if (loop && pontoA !== null && pontoB !== null && pontoB > pontoA && t >= pontoB) irPara(pontoA);
  if (!tocando) return;
  els.forEach(o => {
    if (o.el === mestre) return;
    if (Math.abs(o.el.currentTime - t) > 0.08) { try { o.el.currentTime = t; } catch(e){} }
  });
}, 220);
</script>
</body>
</html>
"""


def gerar(destino: Path, titulo: str, artista: str,
          stems: dict[str, Path], letra_lrc: str | None = None,
          alvo_treino: str | None = None) -> Path:
    """Escreve player.html na pasta da musica."""
    from .letras import analisar_lrc

    ordem = ["instrumental", "vocal", "vocal_principal", "coro", "bateria",
             "bumbo", "caixa", "toms", "chimbal", "pratos", "conducao", "crash",
             "baixo", "guitarra", "piano", "outros"]
    chaves = sorted(stems, key=lambda k: (ordem.index(k) if k in ordem else 99, k))

    lista = []
    for nome in chaves:
        caminho = stems[nome]
        try:
            rel = caminho.relative_to(destino.parent).as_posix()
        except ValueError:
            rel = caminho.name
        lista.append({
            "rotulo": nome.replace("_", " "),
            "arquivo": rel,
            "treino": nome == alvo_treino,
        })

    linhas = analisar_lrc(letra_lrc) if letra_lrc else []

    html = (TEMPLATE
            .replace("__TITULO__", (titulo or "Sem titulo").replace("<", "&lt;"))
            .replace("__ARTISTA__", (artista or "").replace("<", "&lt;"))
            .replace("__STEMS__", json.dumps(lista, ensure_ascii=False))
            .replace("__LETRA__", json.dumps(linhas, ensure_ascii=False)))

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(html, encoding="utf-8")
    return destino
