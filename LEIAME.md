# KaraokeSep

Separa as faixas de uma música e devolve um karaokê com mixer, letra rolando e
pontuação de canto. Windows, offline, sem mensalidade.

---

## Só o player (sem separar no PC)

Se a separação roda no Google Colab, o PC só precisa tocar. Dois cliques em
**`instalar_player.bat`** instalam o necessário em poucos MB (sem PyTorch, sem
modelos, sem gradio), e o **`festa.bat`** abre o player. Se você já usa o
`instalar.bat`, não precisa disso.

No player, a aba **Importar** recebe o zip que o Colab baixa: arraste-o sem
descompactar. Os botões de separar ficam escondidos, porque esse PC não separa.
A atualização pelo botão **Atualizar app** usa a lista curta
(`requirements-player.txt`) enquanto existir o arquivo `modo_player.txt`.

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
**T4 GPU** nas configurações, rode as células em ordem. O zip sai com o nome da música e traz só as faixas
(sem pasta por fora). Arraste-o no campo **Arraste musicas ou o zip do Colab aqui**
(aba Separar, ou Importar no player), ou copie o zip direto para a pasta `saida`:
em qualquer dos dois casos o player descompacta sozinho, cria a pasta com o nome
da música e guarda o zip em `trabalho/importados`. Só entram áudio, letra e
arquivos de partitura; se a música já existir, ele mescla. Sem zip, também dá para
descompactar a mão numa pasta dentro de `saida`.

---

## Partitura da voz e do baixo

**Gerar (no Colab).** No `KaraokeSep_Colab.ipynb`, ligue
`PARTITURA = "voz_e_baixo"` na primeira célula e rode também a célula
**Partitura**, logo abaixo. Ela transcreve a voz e o baixo de cada música e baixa o
zip já com isto dentro de cada pasta:

```
saida/<música>/partituras/
    <faixa>.musicxml        a partitura (abre no MuseScore)
    <faixa>.pdf             para imprimir
    <faixa>.mid             as notas, com o tempo real do áudio
    <faixa>.svg             a partitura na tela do modo festa
    <faixa>.timemap.json    quais notas soam em cada batida
    partitura.json          batidas, compasso, tonalidade, confiança por compasso
```

**Ver (no modo festa).** Músicas com partitura ganham o botão **Partitura**
(tecla `S`) no rodapé, e cada instrumento com partitura ganha um botão no mixer.

- O cursor acompanha o som, destaca a nota e rola a página sozinho.
- Clique numa nota (ou perto dela) para pular a música até ali.
- Compassos em **vermelho** ou **amarelo** são os que a transcrição provavelmente
  errou: o que está escrito não bate com o que se ouve. É alarme, não garantia.
- **Ajuste** adianta ou atrasa o cursor em relação ao som, em milissegundos. Mexa
  se o cursor parecer adiantado ou atrasado na sua caixa de som.
- A linha atual e a próxima da letra aparecem acima da pauta.
- Botões de **PDF**, **MusicXML** e **MIDI** por faixa, e um zip com todos os PDFs.

O ritmo segue as batidas detectadas na música, não um BPM fixo. Se o Beat This!
não puder ser baixado, o Colab usa um rastreador mais simples e o player avisa.

Limites desta versão: só voz e baixo, uma nota por vez, sem tercinas. Piano,
guitarra e bateria ficam para depois. Os limiares de detecção ainda não foram
calibrados em música real: se a voz sair com notas demais ou de menos, os valores
ficam em `AJUSTES`, no começo da célula Partitura.

---

## Instalar como aplicativo

Com o modo festa aberto no Chrome ou no Edge, aparece o botão **Instalar app** no
topo. Ele cria um atalho que abre o modo festa numa janela própria, sem barra de
endereços. O `festa.bat` continua precisando estar aberto: é ele que serve a
música. Se a porta 8137 estiver ocupada, o programa usa outra, e o app
instalado precisa ser reinstalado nela.

---

## Atualizar

O topo mostra a versão instalada. Quando há versão nova no GitHub, aparece o
botão **Atualizar app**. Um clique:

1. baixa a versão nova e confere se ela está inteira e se todos os arquivos
   Python compilam, antes de mexer em qualquer coisa;
2. guarda uma cópia do que vai trocar em `trabalho/backup` (as três últimas);
3. troca os arquivos do programa; se algo falhar, restaura a cópia;
4. instala as bibliotecas novas, se o `requirements.txt` mudou;
5. reinicia o servidor e recarrega a página.

Não são tocados: `venv`, `saida`, `modelos`, `trabalho`, `bin`, o log e o
`tempos.json`. Os `.bat` também ficam como estão (um `.bat` rodando não pode ser
trocado com segurança); se algum mudou, o aviso diz qual, e basta baixá-lo do
GitHub. A atualização não começa enquanto houver separação em andamento.

**Primeira vez.** O botão só existe a partir desta versão. Quem ainda não a tem
baixa o projeto uma vez à mão (no GitHub, **Code → Download ZIP**) e copia a pasta
`app` por cima da sua.

**Repositório privado ou outro ramo.** Ponha um token do GitHub com permissão de
leitura em `github_token.txt`, ao lado do `festa.bat`. Para outro repositório ou
ramo, crie `atualizacao.json` com `{"repo": "dono/nome", "ramo": "main"}`.

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
significa que não carregou; o aviso no rodapé diz o motivo (formato inválido,
arquivo não encontrado, rede) e o tamanho que o servidor enxerga. Clique em Refazer. Se persistir, rode
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
