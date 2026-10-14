# KaraokeSep

Separa as faixas de uma música e devolve um karaokê com mixer, letra rolando e
pontuação de canto. Windows, offline, sem mensalidade.

---

## Começando do zero

1. Instale o **Python 3.12** em
   [python.org](https://www.python.org/downloads/release/python-3129/),
   marcando **Add python.exe to PATH**.
   O 3.13 e o 3.14 não servem: uma das bibliotecas não compila neles.
2. Dois cliques em **`instalar.bat`**. São mais de 2 GB de download.
   Se não houver Python compatível, ele se oferece para instalar pelo winget.
3. Dois cliques em **`festa.bat`**.

Na primeira separação ele ainda baixa os pesos do modelo. Acontece uma vez só.

---

## Os atalhos

| Arquivo | O que faz |
|---|---|
| **festa.bat** | abre o modo festa. É o do dia a dia |
| rodar.bat | abre o programa com abas, para ajustes finos |
| diagnostico.bat | diz por que uma faixa não aparece no mixer |
| faxina.bat | apaga duplicatas e mixagens antigas |
| instalar.bat | instalação e reparo do ambiente |
| verificar_modelos.bat | lista os modelos disponíveis na máquina |

---

## Modo festa

Abre no navegador, em `127.0.0.1`. **Não abra o `festa.html` com dois cliques**
— aberto direto do disco, o navegador silencia o processamento de áudio.

Quatro abas na lateral:

- **Fila** — as músicas, com botões por item: separar faixas, buscar letra,
  separar vozes de um dueto
- **Separar** — modo, envio de músicas arrastando, progresso, cancelar
- **Letra** — busca no LRCLIB com artista e música editáveis, ou colar um `.lrc`
- **Ajuda** — como pontuar bem e o que fazer quando algo sai errado

No rodapé: botão de voz em quatro níveis, mixer por instrumento, espectro ao
vivo com filtro de frequência, pontuação de canto, esconder espectro e tela
cheia.

Atalhos: `espaço` toca, `←→` pula 5 s, `↑↓` mudam a voz, `n`/`p` trocam de
música, `e` esconde o espectro, `f` tela cheia.

### Dois motores

**Com faixas separadas** — ganho independente por instrumento. Qualidade máxima,
CPU parada.

**Sem faixas** — cancelamento de centro ao vivo, só na banda da voz de 150 Hz a
8 kHz, preservando grave e agudo. Funciona em gravação com voz seca no centro;
falha quando a voz tem alargamento estéreo ou reverb nos lados.

---

## Os modos de separação

| Modo | Faixas | Observação |
|---|---|---|
| Karaokê | 2 | voz e instrumental |
| Padrão | 4 | **o recomendado.** Mais rápido e melhor que o karaokê |
| Seis | 6 | acrescenta guitarra e piano; o stem "outros" piora bastante |
| Profundo | 10 | bateria em peças e voz separada do coro |

Sem placa NVIDIA, o Profundo empilha três passadas com modelos pesados e pode
passar de duas horas por música. As estimativas mostradas na tela são **medidas
na sua máquina** e ficam em `tempos.json`.

Baixo e bateria separam muito bem. Guitarra dá para usar. Piano decepciona.

---

## Pontuação de canto

Compara a altura da nota que você canta com a da voz original, a cada 50 ms.

- A oitava não conta
- Até um semitom é ponto cheio
- Trecho instrumental é de graça
- Silêncio onde a voz original canta vale zero

Ligar a pontuação **desliga a voz original** — senão o microfone ouviria a
gravação e pontuaria ela.

É brincadeira, não avaliação: quem canta com vibrato e expressão pontua pior
que quem canta reto.

**O microfone de cantar vai direto na caixa**, nunca no PC. Passando pelo
computador, o Windows devolve o som com 100 a 250 ms de atraso.

---

## Separar mais rápido: Google Colab

Sem placa NVIDIA, a separação leva minutos por música. O arquivo
`KaraokeSep_Colab.ipynb` roda na placa do Google, de graça, em menos de um
minuto por música — e usa modelos melhores.

Abra em [colab.research.google.com](https://colab.research.google.com), ligue a
**T4 GPU** nas configurações, rode as células em ordem e descompacte o zip
dentro de `saida`.

---

## O programa com abas

`rodar.bat`, para o que o modo festa não cobre:

- **Separar** — lote com opções detalhadas
- **Diagnóstico** — mede o corte real de banda pelo espectro e denuncia MP3 de
  128 kbps reetiquetado como 320
- **Editor espectral** — limpeza automática de vazamento e edição por região de
  tempo e frequência
- **Ferramentas** — reforçar o volume de uma faixa, mudar o tom sem mudar a
  velocidade

---

## Quando algo sai errado

**Som metálico ou abafado** — faixa tocando em dobro. Rode `faxina.bat`.

**Faltando instrumento** — olhe a bolinha de cada canal no mixer. Vermelha
significa que não carregou; clique em Refazer. Se persistir, rode
`diagnostico.bat`.

**O teste que vale:** com todos os faders em 0 dB, a música deve soar igual ao
arquivo original.

**Nada aparece na fila** — as músicas ficam em `saida`, uma pasta por música.

**A letra não é encontrada** — corrija artista e música nos campos da aba Letra.
Nome vindo de site de download quase nunca casa.

---

## Levar para outro PC

Copie a pasta **sem** o `venv` e rode o `instalar.bat` lá. A pasta `modelos`
pode ir junto e evita baixar os pesos de novo.

O `venv` não pode ser copiado: as DLLs do PyTorch quebram fora da máquina onde
foram instaladas.

---

## Limitações honestas

- Nenhuma separação é perfeita. Sempre sobra vazamento entre as faixas.
- Piano e guitarra são os stems mais fracos.
- Duas vozes de timbre parecido não separam.
- Transposição acima de 5 semitons deixa o timbre artificial.
- Stems de música comercial são para uso pessoal.
