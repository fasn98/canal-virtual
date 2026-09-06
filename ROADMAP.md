# ROADMAP / notas de produção

## Apresentador MuseTalk — idle v3 (2026-09-06)

Idle novo (LivePortrait Run A, `--animation-region exp`) a partir de foto real
do Fabio: terno escuro, colarinho aberto sem gravata, expressão neutra-séria,
enquadrado 1024×1024 (o anterior era 572×542). No pod:
`MT_IDLE_VIDEO=/workspace/LivePortrait/animations/idle_v3_20260906_1925/idle_v3_muted.mp4`,
`MT_AVATAR_ID=avatar_idle_v3_srv`. No Contabo (`.env`, gitignored):
`MUSETALK_PRESENTER_SCALE=0.39` (era 0.70), `RUNPOD_POD_ID`/`GPU_SERVER_URL` →
pod `ah7gg7t3lm2h7j`.

### Observações que viraram lição

1. **O "fantasma da bancada" já existia em produção antes do v3.** A saia de
   `studio_desk.png` era 96% opaca (`PANEL_A=245`) e a faixa de vidro logo abaixo
   do rim 63–78%; o apresentador chroma-keyed (terno até a borda do frame do
   idle) vazava por baixo. Com o idle 572×542 @ SCALE 0.70 eram ~43 px de terno
   abaixo do rim — **não foi regressão do idle 1024²**. Corrigido tornando a
   bancada 100% opaca do rim pra baixo.

2. **Cache de blocos (`volumes/assets/musetalk/`) tem que ser limpo sempre que o
   idle muda.** A pipeline remonta a composição a partir de blocos já
   sintetizados (por id) sem re-sintetizar — troca de idle sem limpar o cache =
   avatar antigo no ar silenciosamente. (Preservar assets fixos como
   `promo-futureverse-beyond-v3.mp4`.)

3. **`gpu:active_pod_id` (Redis) tem precedência sobre `.env RUNPOD_POD_ID`.**
   `_runpod_pod_id()` = Redis `or` env. Só o failover (`_set_active_pod_id`,
   `lib/gpu_client.py`) reescreve a chave, e sempre para um pod novo funcional.
   Trocar de pod exige atualizar **os dois** (Redis + `.env`).

### Candidatos a check automático (futuro "diretor de produção")

- **`MT_AVATAR_ID` ativo tem que ter pasta em `results/v15/avatars/`.** Um
  `fresh_prep=False` no log do `musetalk_worker` **sem** a pasta correspondente
  indica que a variável não chegou ao worker (edição do `start_all.sh` não
  propagou / worker não reiniciou).
- Após trocar o idle: `volumes/assets/musetalk/` só deve conter assets fixos
  conhecidos; qualquer hash `[0-9a-f]*.mp4` remanescente é cache velho.
- `RUNPOD_API_KEY` do `.env` tem que autenticar no pod ativo (`podResume` não
  pode dar 401) — senão todo bloco MuseTalk cai no fallback D-ID sem alarde.

### Melhoria de arquitetura pendente

**Alternância de âncoras (feminina / Fabio por período)** exige `MT_AVATAR_ID`
**por requisição** (parâmetro no `/lipsync` ou `/generate`), não fixo no boot do
worker. Hoje trocar de âncora = editar `start_all.sh` + `stop_all/start_all` +
prep de 2–4 min. Bloqueia qualquer grade com mais de um apresentador.
