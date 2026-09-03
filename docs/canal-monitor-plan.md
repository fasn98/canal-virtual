# Monitor 24h do Canal Virtual — plano de implementação

**Objetivo:** manter o canal ao vivo 24h/dia indefinidamente, com um dashboard
simples (poucos cliques) para reiniciar a transmissão, injetar notícia
bombástica e observar a saúde do pipeline.

**Decisões de projeto (2026-09-01):**
1. Camada de controle = **`ops-agent` só de saída** (container na VM que só disca
   pra fora; nada novo exposto; `metrics-api` continua read-only).
2. Watchdog = **auto-remediar restart + alertar** (reinicia streamer/renderer
   sozinho, com trava anti-loop e limite/hora; **não** recria broadcast no
   YouTube — isso só alerta).
3. Notícia bombástica = **flag de prioridade** (`priority=breaking`) que fura a
   fila: `synthesizer` ignora o freio de orçamento e `commentator` faz
   fast-track (pula os 3 revisores) para itens `breaking`.
4. Dashboard = **página nova `/canal`** no app Replit "YouTube Optimizer"
   (`replId 5a9d4740-06f8-4c06-a124-c4ffe1e0b187`).

---

## Arquitetura

```
VM (Contabo)                                   Replit "YouTube Optimizer"
------------                                   --------------------------
pipeline … renderer … streamer
metrics-api :8090  (LEITURA, inalterado) ──GET──► /api/canal/status (proxy, cache 3s)
ops-agent (NOVO, sem portas):
  • watchdog loop (docker :rw + redis local)
  • long-poll  ─────────────────────────────GET──► /api/canal/commands/next
  • executa whitelist                            (fila em Postgres)
  • POST resultado + heartbeat ────────────POST──► /api/canal/agent/report
                                                  /canal  (página do dashboard)
                                                  segredos server-side apenas
```

Browser fala só com o backend Replit. `ops-agent` só faz conexão de saída
(mesmo modelo de `lib/tubeoptimizer_client.py`).

---

## Contrato ops-agent ↔ Replit

Auth: header `X-Canal-Agent-Secret: <CANAL_OPS_SHARED_SECRET>` nas duas rotas.

### `GET /api/canal/commands/next`
Long-poll (até ~25s). Resposta:
```json
{ "command": { "id": "cmd_...", "action": "restart_stream", "params": {}, "requested_by": "fasn98", "requested_at": "..." } }
```
ou `{ "command": null }` no timeout. Devolve **um** comando por vez, marca-o
`dispatched`.

### `POST /api/canal/agent/report`
```json
{
  "heartbeat_at": "...",
  "status_snapshot": { ...ver abaixo... },
  "command_result": { "id": "cmd_...", "ok": true, "detail": "streamer restarted", "finished_at": "..." }  // opcional
}
```

### status_snapshot (o agente monta a cada tick, ~20s)
```json
{
  "stream": { "streamer_running": true, "streamer_restarts_1h": 0, "final_mp4_age_sec": 41,
              "youtube_live": true, "youtube_checked_at": "..." },
  "pipeline": { "containers": {"renderer":"running", ...}, "missing": [],
                "renderer_last_emission_age_sec": 34,
                "xpending": {"renderer-group": 0, "classifier-group": 1, ...} },
  "worldin3": { "next_fire": "...", "last_upload": {...}, "elevenlabs_used_today": 0 },
  "watchdog": { "last_action": null, "actions_1h": 0, "alerts": [] }
}
```
(Orçamentos ElevenLabs/Claude/D-ID a página lê direto do `metrics-api`, não
precisam ir no snapshot.)

---

## Ações (whitelist FIXA no ops-agent — nada dinâmico)

| action | efeito | guarda |
|---|---|---|
| `restart_stream` | `docker restart streamer` | recusa se < 60s do último restart_stream; máx 4/h |
| `restart_service` | `docker restart <params.name>` | `name` ∈ {renderer, synthesizer, commentator, classifier, collector, promoter}; máx 4/h por serviço |
| `inject_breaking` | valida `title`+`summary` → id formato produção → `XADD news.raw {id,title,summary,link,source:"Redação",published:now,priority:"breaking",injected_by,injected_at}` | `title` ≤ 180 chars, `summary` ≤ 600; máx 6/h |

Todo comando + resultado grava linha em `canal_audit`.

---

## Watchdog (loop no ops-agent, tick ~20s)

Sinais e reações (política "auto-remediar restart + alertar"):

| sinal | condição | ação automática | alerta |
|---|---|---|---|
| renderer travado | `renderer_last_emission_age_sec` > `WD_RENDERER_STALE_SEC` (def. 900) | `docker restart renderer` (máx 3/h) | push |
| streamer fora | container não `running` por > 60s | `docker restart streamer` (máx 4/h) | push |
| streamer em loop | `streamer_restarts_1h` ≥ 5 | **não** reinicia (já está em loop) | push URGENTE |
| final.mp4 velho | `final_mp4_age_sec` > `WD_FINAL_STALE_SEC` (def. 1800) | `docker restart renderer` 1x | push |
| consumer preso | `xpending` de um grupo > `WD_XPENDING_MAX` (def. 50) e subindo | `docker restart <consumidor>` (máx 2/h) | push |
| broadcast fora | YouTube `lifeCycleStatus` ≠ `live`/`testing` | **nenhuma** (só alerta) | push URGENTE |
| agent morto | sem heartbeat > 90s (detectado no lado Replit) | — | página mostra "AGENT OFFLINE" |

Limites `WD_*` e `*_PER_HOUR` via env do serviço `ops-agent`. Circuit breaker
global: se o watchdog fez > `WD_MAX_ACTIONS_1H` (def. 10) ações numa hora, ele
**para de agir** e só alerta até a janela limpar.

---

## Dashboard `/canal` (React, dentro de artifacts/youtube-optimizer)

- **Pílula topo:** `NO AR` / `DEGRADADO` / `FORA` + "última emissão há Xs".
- **Tiles:** Transmissão · Pipeline (9 containers) · Orçamentos (ElevenLabs/Claude/D-ID, do metrics-api) · worldin3 (próximo disparo, último upload).
- **Botões** (confirm dialog em cada): Reiniciar transmissão · Reiniciar renderer · Notícia bombástica (form: título + resumo) · Reiniciar serviço… (select da whitelist).
- **Timeline:** últimas 20 linhas de `canal_audit` + últimos N blocos ao ar.
- Auto-refresh 5s (TanStack Query `refetchInterval`), backend cacheia chamadas à VM por ~3s.

---

## Fases de entrega

### Fase 0 — infra Replit (sem tocar produção)
- [ ] tabelas Drizzle `canal_commands`, `canal_audit` (+ `push-force`)
- [ ] rotas `/api/canal/status`, `/api/canal/commands` (enqueue), `/api/canal/commands/next`, `/api/canal/agent/report`, `/api/canal/audit`
- [ ] OpenAPI + `pnpm codegen`
- [ ] secrets: `CANAL_METRICS_URL`, `CANAL_METRICS_API_KEY`, `CANAL_OPS_SHARED_SECRET`
- [ ] página `/canal` só-leitura (status + tiles + timeline), sem botões ainda

### Fase 1 — ops-agent read-only (sem tocar produção além de subir 1 container sem portas)
- [ ] `ops-agent/` (Dockerfile + agente Python): monta `status_snapshot`, manda heartbeat, **ainda não executa comandos nem watchdog**
- [ ] serviço `ops-agent` no `docker-compose.yml` (socket `:rw`, sem `ports:`, `profiles` não — sobe no `up`)
- [ ] validar snapshot chegando no dashboard

### Fase 2 — ações manuais
- [ ] ops-agent passa a consumir a fila e executar a whitelist (`restart_stream`, `restart_service`)
- [ ] botões no dashboard ligados (com confirm)
- [ ] `canal_audit` populando

### Fase 3 — watchdog
- [ ] loop de watchdog no ops-agent em modo **alerta-only** primeiro (1–2 dias observando)
- [ ] ligar auto-remediação (restart) com os limites/hora + circuit breaker
- [ ] push notifications (canal a definir: e-mail? Telegram via Zernio? webhook?)

### Fase 4 — notícia bombástica com prioridade
- [ ] `synthesizer/main.py`: item com `priority=="breaking"` fura o freio de orçamento diário
- [ ] `commentator`: `priority=="breaking"` → fast-track (pula os 3 revisores; comentário direto)
- [ ] `classifier`: propaga o campo `priority` adiante
- [ ] `inject_breaking` no ops-agent + form no dashboard
- [ ] teste ponta-a-ponta com item real marcado breaking

### Fase 5 — endurecer
- [ ] rate-limit nas rotas Replit, retry/backoff no agente
- [ ] "AGENT OFFLINE" no dashboard quando heartbeat > 90s
- [ ] métricas do próprio monitor (quantas remediações/semana)
- [ ] runbook: o que fazer quando o watchdog alerta "broadcast fora"

---

## Riscos / notas

- **Broadcast do YouTube caindo** é o cenário que o watchdog NÃO resolve sozinho
  (decisão 2). Histórico do streamer mostra que recriar broadcast
  automaticamente já causou duplicatas e auto-encerramento — fica manual, com
  alerta urgente.
- Mudanças de Fase 4 tocam `synthesizer` e `commentator` (serviços de produção)
  → precisam de commit + `docker compose up -d --build` desses serviços.
- `ops-agent` com socket Docker `:rw` é o único componente privilegiado novo.
  Mitigação: sem portas (só saída), whitelist fixa de ações, sem `eval`/shell
  dinâmico, tudo auditado.
- `CANAL_OPS_SHARED_SECRET` é a credencial que protege a fila de comandos. Se
  vazar, um atacante consegue enfileirar `restart_*`/`inject_breaking` (não
  consegue rodar comando arbitrário — a whitelist é fixa no agente).
