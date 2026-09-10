# ROADMAP / notas de produção

## Apresentador MuseTalk — idle v3 (2026-09-06)

Idle novo (LivePortrait Run A, `--animation-region exp`) a partir de foto real
do Fabio: terno escuro, colarinho aberto sem gravata, expressão neutra-séria,
enquadrado 1024×1024 (o anterior era 572×542). No pod:
`MT_IDLE_VIDEO=/workspace/LivePortrait/animations/idle_v3_20260906_1925/idle_v3_muted.mp4`,
`MT_AVATAR_ID=avatar_idle_v3_srv`. No Contabo (`.env`, gitignored):
`MUSETALK_PRESENTER_SCALE=0.39` (era 0.70), `RUNPOD_POD_ID`/`GPU_SERVER_URL` →
pod `ah7gg7t3lm2h7j`, `RUNPOD_API_KEY` rotacionada 2026-09-07 (a anterior dava
401 no pod novo). `studio_desk.png` regerado 100% opaco do rim pra baixo.

### Observações que viraram lição

1. **O "fantasma da bancada" já existia em produção antes do v3.** A saia de
   `studio_desk.png` era 96% opaca (`PANEL_A=245`) e a faixa de vidro logo abaixo
   do rim 63–78%; o apresentador chroma-keyed (terno até a borda do frame do
   idle) vazava por baixo. Com o idle 572×542 @ SCALE 0.70 eram ~43 px de terno
   abaixo do rim — **não foi regressão do idle 1024²**. Corrigido tornando a
   bancada 100% opaca do rim pra baixo (`PANEL_A=255`, faixa de vidro `255`,
   linha fina do rim `255`; glow ACIMA do rim inalterado).

2. **Cache de blocos (`volumes/assets/musetalk/`) tem que ser limpo sempre que o
   idle muda.** A pipeline remonta a composição a partir de blocos já
   sintetizados (por id) sem re-sintetizar — troca de idle sem limpar o cache =
   avatar antigo no ar silenciosamente. (Preservar assets fixos como
   `promo-futureverse-beyond-v3.mp4`.)

3. **`gpu:active_pod_id` (Redis) tem precedência sobre `.env RUNPOD_POD_ID`.**
   `_runpod_pod_id()` = Redis `or` env. Só o failover (`_set_active_pod_id`,
   `lib/gpu_client.py`) reescreve a chave, e sempre para um pod novo funcional.
   Trocar de pod exige atualizar **os dois** (Redis + `.env RUNPOD_POD_ID` +
   `.env GPU_SERVER_URL`).

## Ciclo do pod GPU — `gpu_session()` (diagnóstico 2026-09-06)

**Lifecycle no caminho de produção:** `main.py:_render_musetalk_block` →
`compose_presenter_block` → `get_presenter_video` → **`with gpu_session()`**
(renderer/musetalk.py:274), incondicional. `start_gpu()` na entrada, `stop_gpu()`
no `finally`. `--no-lifecycle` existe **só** no CLI de debug `python3
lib/gpu_client.py` (linhas ~1587/1624) — não é alcançável pelo renderer. Pod
ligado fora de ciclo = resíduo de `stop_all/start_all` manual, não furo do
lifecycle.

**Custo por ciclo (medido, `volumes/output/gpu_client.log`, 3 ciclos):**
7,37 / 7,85 / 8,29 min pod-on (blocos curtos, pré-chunking).
- `podResume`→RUNNING: 12–26 s
- RUNNING→inferência pronta: +~100 s (worker `_load()`: models ~71 s + Whisper +
  FaceParsing + prep do avatar **do cache**). **Boot+prep ≈ 125–130 s.**
- Bloco longo real (152 s vídeo, 7 chunks): geração medida = **574 s** (teste
  2026-09-06, sem boot). Ciclo completo projetado ≈ **~12 min pod-on**.

**Autonomia com US$4,26 / 5.332 sats** ($0,25/h GPU RTX A4000 = 313 sats/h):
- Pod parado (só Network Volume 100 GB, ~$0,07/GB/mês → ~$0,0097/h — **confirmar
  no billing**): **~18 dias**.
- Janela contínua (GPU + storage ≈ $0,26/h): **~16,4 h**.
- Ciclando por bloco: 8–12 min pod-on → $0,033–0,050 GPU/bloco → ~85–128 blocos.

**Cadência do canal (streamer, medido):** 1 bloco a cada **~154 s** (~24/h,
~560/dia). Geração MuseTalk ≈ 3,8–5× tempo real + boot → **1 pod não acompanha
1:1**. MuseTalk é caminho premium/intermitente (janela + N blocos/dia), resto
D-ID/estático + reprise.

## Economia: D-ID/estático vs MuseTalk por bloco (medido 2026-09-06/07)

**D-ID está DESLIGADO** (`ENABLE_LIPSYNC=false`, env + compose). O "caminho D-ID"
hoje = **áudio ElevenLabs + imagem estática + ffmpeg local**. Zero chamada à API
do D-ID, zero crédito D-ID.

| | D-ID/estático fresh | D-ID/estático reprise | MuseTalk fresh | MuseTalk cache-hit |
|---|---|---|---|---|
| $ marginal | ElevenLabs ~$0,30–0,40¹ | **$0** | GPU **$0,033–0,050**² | **$0** |
| API externa | ElevenLabs | nenhuma | nenhuma (Chatterbox no pod) | nenhuma |
| tempo de geração | ~1–2 min CPU³ | ~1–2 min CPU³ | **~8–12 min** (boot+GPU) | ~1–2 min |
| GPU | não | não | 1 pod 8–12 min | não |
| acompanha ~154 s? | sim | sim | **não** | sim |
| avatar | estático (não fala) | estático | lábios + micro-expressão | — |

¹ plano ElevenLabs a confirmar. `eleven_multilingual_v2`, `DAILY_CREDIT_BUDGET=4000`
(1 crédito = 1 char). Bloco fresh ~1600–1826 chars. 4000/dia → **~2 blocos fresh
de áudio/dia**; resto reprise (áudio já pago, $0). 06/09: 35 comentários frescos
(texto), `metrics:reprise=12`, só ~2 áudios frescos.
² 8–12 min pod-on medido × $0,25/h.
³ estimado (1 passada ffmpeg `-preset ultrafast` 1080p) — falta 1 medição limpa
do delta `Processando → SUCESSO EMISSÃO` (buffer de log estava vazio).

**Leitura:** o canal hoje é limitado pelo **orçamento ElevenLabs (~2 blocos
fresh/dia)**, não pelo custo de avatar. MuseTalk **não toca** esse orçamento
(TTS on-pod, grátis por char) e sai **~10× mais barato por bloco fresh** que a
ElevenLabs ($0,04 vs $0,35) — o preço é o **tempo** (8–12 min) e o teto de
throughput (~5–7 blocos MuseTalk/h por pod). Janela contínua de pod: ~$0,26/h,
~5–7 blocos MuseTalk/h nesse período, resto D-ID/reprise. Saldo $4,26 ≈ ~16 h de
janela.

## Candidatos a check automático (futuro "diretor de produção")

- **`MT_AVATAR_ID` ativo tem que ter pasta em `results/v15/avatars/`.** Um
  `fresh_prep=False` no log do `musetalk_worker` **sem** a pasta correspondente
  indica que a variável não chegou ao worker (`start_all.sh` não propagou /
  worker não reiniciou).
- Após trocar o idle: `volumes/assets/musetalk/` só deve conter assets fixos
  conhecidos; qualquer hash `[0-9a-f]*.mp4` remanescente é cache velho → limpar.
- `RUNPOD_API_KEY` do `.env` tem que autenticar no pod ativo (`podResume` sem
  401) — senão todo bloco MuseTalk cai no fallback D-ID sem alarme.
- `gpu_session()` só deve ligar o pod dentro do ciclo; pod RUNNING sem `=== ciclo
  GPU: START ===` recente no `gpu_client.log` = ligado à toa (queimando GPU).
- Instrumentar o delta `Processando → SUCESSO EMISSÃO` no renderer (tempo de
  geração D-ID/estático) — hoje só estimado.

## Decisão pendente: TTS Chatterbox (on-pod) vs ElevenLabs

Diagnóstico 2026-09-07: o gargalo do canal é o orçamento ElevenLabs
(`DAILY_CREDIT_BUDGET=4000` → ~2 blocos fresh/dia), **não** o avatar. Chatterbox
no pod é grátis por char (validado: 1943 chars / 7 chunks / QA OK). Hoje
`get_presenter_video` já usa Chatterbox nos blocos MuseTalk — **o mp3 fresh que
o synthesizer pagou na ElevenLabs para esses blocos é descartado**.

### (a) Sem decisão editorial — fazer assim que der: synthesizer ciente do caminho do bloco

Objetivo: não gastar crédito ElevenLabs em áudio que o MuseTalk vai descartar.

**Exige:**
1. **Synthesizer ler o modo de avatar.** Passar `AVATAR_PROVIDER`,
   `MUSETALK_ALLOW_ON_AIR`, `GPU_SERVER_URL`, `INFERENCE_SERVER_API_KEY` ao
   container `synthesizer` (docker-compose — hoje só o `renderer` recebe).
2. **Skip da ElevenLabs quando o bloco vai por MuseTalk.** Em `synthesizer/main.py`:
   se `AVATAR_PROVIDER==musetalk` E `MUSETALK_ALLOW_ON_AIR` E `GET /ready`==200
   no pod → **não chama a ElevenLabs**; publica `news.ready` com `audio_file`
   apontando para um **mp3 de reprise já pago** (fallback-only) + marcador
   `tts=chatterbox`. (~20 linhas + o probe.)
3. **Fallback de áudio quando o MuseTalk falha.** `_render_musetalk_block` no
   `except` cai em `_render_did_block`, que usa `data["audio_file"]`. Com o
   ponteiro de reprise do passo 2, o fallback estático toca um bloco reprisado
   coerente (mesmo comportamento de hoje quando o orçamento estoura) a $0 — em
   vez de `DUMMY_AUDIO` (conteúdo errado). Sem o ponteiro, o `except` teria que
   disparar reprise explicitamente.
4. **Métrica** `elevenlabs:skipped:musetalk` para medir a economia.

Sem API nova, sem decisão editorial. Risco principal: o caminho de fallback de
áudio (passo 3) — acertar isso e é seguro. Urgência acoplada a (b): só morde
quando o MuseTalk de fato funciona numa janela (pod ligado). Construir pronto.

### (c) Correções ao diff do Passo 3 (documentadas 2026-09-10, NÃO implementadas)

**Pronúncia pt-BR do Chatterbox NÃO é bloqueador.** Investigação 2026-09-10:
falso alarme. O worker (`chatterbox_worker.py`) já chama
`model.generate(text, language_id=LANG)` com `LANG = os.environ.get("TTS_LANG",
"pt")` (linha 33/113), impl `multilingual`. Texto real acentuado da pipeline
(1704–1784 chars, chunkado, 6–7 blocos, pod RTX 4090) → áudio com ç/acento/nasal
correto, validado de ouvido. O "bug" veio do fixture `p2_text.txt` que eu montei
em ASCII puro ("edicao/nao/atencao") no Passo 2 — não da pipeline. Nada a
corrigir no TTS.

**Correção 1 — o gate do synthesizer tem que espelhar o do renderer.**
Roteamento do renderer (`renderer/main.py:481`):
`AVATAR_PROVIDER=="musetalk" and (not on_air or MUSETALK_ALLOW_ON_AIR)`, onde
`not on_air == is_test_item(...) and not ALLOW_TEST_ON_AIR`. O rascunho original
do gate do synthesizer barrava o skip em item de teste a menos que
`ELEVENLABS_SKIP_ALLOW_TEST=true` — **não espelha**: com `MUSETALK_ALLOW_ON_AIR=
false` (estado normal) o renderer manda item de teste pro MuseTalk, mas o
synthesizer chamaria a ElevenLabs → o mp3 é descartado. Gate correto:

```
def _musetalk_will_handle(news_id, title):
    if AVATAR_PROVIDER != "musetalk":
        return False
    testish = is_test_id(news_id, title)
    # espelha renderer/main.py:481 → (not on_air or MUSETALK_ALLOW_ON_AIR)
    routes = (testish and not ALLOW_TEST_ON_AIR) or MUSETALK_ALLOW_ON_AIR
    if not routes:
        return False
    return _pod_ready()   # AND extra: só pula ElevenLabs se o pod puder atender
```

Exige passar `ALLOW_TEST_ON_AIR` ao container `synthesizer` (hoje só o renderer
recebe). A assimetria do `_pod_ready()` é proposital: o renderer roteia item de
teste pro MuseTalk incondicionalmente (tem fallback D-ID se o pod cair); o
synthesizer tem que ser conservador — só pula a ElevenLabs se estiver confiante
que o MuseTalk produz áudio, senão o fallback fica sem áudio fresco.

**Correção 2 — `ELEVENLABS_SKIP_ALLOW_TEST` deixa de ser necessário.** Com a
correção 1, item de teste + pod pronto → o gate já retorna True → skip
automático (era exatamente o que a flag fazia manualmente). Item de teste + pod
fora → gate False → ElevenLabs normal → fallback D-ID com o mp3 fresco. A
validação "sem ir ao ar" que a flag habilitava já acontece sozinha: pod ligado +
injeta item de teste → skip ocorre, e item de teste nunca toca `final.mp4` de
produção (vai pra `final_test.mp4`). **Remover do diff**: a env no
docker-compose, a leitura no synthesizer e o ramo do gate que a referenciava. Se
algum dia quiser *forçar* ElevenLabs num item de teste (ex.: exercitar o
fallback D-ID com áudio certo), isso é `AVATAR_PROVIDER=d-id` temporário ou uma
flag `ELEVENLABS_FORCE` dedicada — não uma "skip-allow". Não adicionar
especulativamente.

**Diff do Passo 3 revisado (net):**
- `docker-compose.yml` (env do `synthesizer`): + `AVATAR_PROVIDER`,
  `MUSETALK_ALLOW_ON_AIR`, `ALLOW_TEST_ON_AIR`, `GPU_SERVER_URL`,
  `INFERENCE_SERVER_API_KEY`. (sai `ELEVENLABS_SKIP_ALLOW_TEST`)
- `synthesizer/main.py`: `_pod_ready()` (probe `/ready`, cache ~20s) +
  `_musetalk_will_handle()` (gate espelhado, sem ramo de flag) + bloco de skip
  em `handle_event` antes de `synthesize_audio` (publica `news.ready` com
  ponteiro de reprise já pago + `tts_engine=chatterbox`, `bump_metric(
  "elevenlabs:skipped:musetalk")`, xack, return). ~50 linhas.
- `renderer/musetalk.py`: `PresenterFallbackError` + preservar o WAV aprovado no
  QA (não apagar em `finally` antes do QA do MP4). Inalterado do rascunho.
- `renderer/main.py`: `_render_musetalk_block` `except` em 2 modos (WAV
  Chatterbox aprovado → estático com esse áudio; sem WAV → segura o bloco).
  Inalterado do rascunho.

Nada implementado. Aguarda OK.

### (b) Decisão editorial pendente (do usuário): janela Chatterbox = "turno do Fabio"

A voz Chatterbox é o clone do Fabio (`audio_fabio_v2.wav`); a voz ElevenLabs
`ZP7ct…` é a âncora feminina que o caminho estático mostra. **Migrar áudio fresh
para o Chatterbox = assumir o Fabio como âncora** naquele período. Conecta com a
"Alternância de âncoras" abaixo: a janela de pod ligado pode ser exatamente o
**turno do Fabio** (Chatterbox TTS + MuseTalk lip-sync em parte dos blocos),
com âncora feminina + ElevenLabs no resto do dia. Nesse turno o teto de 4000
créditos/dia fica irrelevante e o custo marginal cai para ~$0,26/h de pod.
Não decidido — depende da grade de âncoras.

## Melhoria de arquitetura pendente

**Alternância de âncoras (feminina / Fabio por período)** exige `MT_AVATAR_ID`
**por requisição** (parâmetro no `/lipsync` ou `/generate`), não fixo no boot do
worker. Hoje trocar de âncora = editar `start_all.sh` + `stop_all/start_all` +
prep de 2–4 min. Bloqueia qualquer grade com mais de um apresentador.

## Dívida técnica

**Sem heartbeat para seca de notícia** (diagnosticado 2026-09-10) — a pipeline
trava no último bloco indefinidamente quando o feed para de produzir, mesmo com
orçamento disponível. O reprise só cobre **orçamento estourado**
(`budget_exceeded=True` dispara `pick_reprise_item()` dentro de `handle_event`,
que só roda quando chega um item em `news.final`); quando a própria notícia seca,
`handle_event` nem roda. Visto na madrugada de 2026-09-10: último `news.final`
00:52 UTC, `collector` em "0 novos" por >1 h, `final.mp4` parado, índice de
reprise saudável (1 item hoje) mas nunca consultado. Precisa de: **timeout desde
o último `news.final` → forçar reprise mesmo sem `budget_exceeded`**.
