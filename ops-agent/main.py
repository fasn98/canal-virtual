"""
ops-agent — agente de monitoramento 24h do canal virtual.

Roda DENTRO da VM, junto ao pipeline. Só faz conexões de SAÍDA:
  - lê o metrics-api e o Redis pela rede interna (canalnet);
  - lê o estado dos containers pelo socket do Docker;
  - POSTa um "snapshot" de saúde + heartbeat para o dashboard /canal hospedado
    no app Replit (TUBEOPTIMIZER), autenticando com CANAL_OPS_SHARED_SECRET.

NÃO expõe porta nenhuma. Nada novo fica acessível de fora da VM.

FASE 1 (este arquivo): só o laço de relatório. NÃO consome a fila de comandos
e NÃO executa nenhuma ação (o watchdog vem marcado enabled=false). As Fases 2 e
3 acrescentam a execução da whitelist (restart_*, inject_breaking) e o watchdog.
Ver docs/canal-monitor-plan.md.
"""

import datetime
import hashlib
import os
import time
import traceback

import redis as redis_lib
import requests

try:
    import docker  # SDK oficial; mesma lib que o metrics-api usa
except Exception:  # pragma: no cover - só falha se a imagem não instalou
    docker = None

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


# --- Config (tudo via env, definido no serviço ops-agent do docker-compose) ---
APP_BASE_URL = os.environ.get(
    "CANAL_APP_BASE_URL", "https://tube-oprtimizer.replit.app"
).rstrip("/")
OPS_SECRET = os.environ.get("CANAL_OPS_SHARED_SECRET", "").strip()

if not OPS_SECRET:
    print(
        "[ops-agent] FATAL: CANAL_OPS_SHARED_SECRET não definido no .env da VM. "
        "Use `openssl rand -hex 32` e coloque O MESMO valor aqui e no secret "
        "CANAL_OPS_SHARED_SECRET do app Replit. Encerrando.",
        flush=True,
    )
    raise SystemExit(1)

METRICS_URL = os.environ.get("METRICS_API_URL", "http://metrics-api:8090").rstrip("/")
METRICS_KEY = os.environ.get("METRICS_API_KEY", "").strip()

REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "canal-virtual")
FINAL_MP4_PATH = os.environ.get("FINAL_MP4_PATH", "/app/output/final.mp4")

REPORT_INTERVAL_SEC = int(os.environ.get("OPS_AGENT_REPORT_INTERVAL_SEC", "20"))
HTTP_TIMEOUT_SEC = float(os.environ.get("OPS_AGENT_HTTP_TIMEOUT_SEC", "15"))

# Heurística de "no ar" enquanto a Fase 3 não checa o YouTube de verdade:
# streamer up + final.mp4 renovado há menos disso.
STREAM_LIVE_MAX_AGE_SEC = int(os.environ.get("OPS_AGENT_STREAM_LIVE_MAX_AGE_SEC", "600"))

# worldin3 — só para calcular o próximo disparo e ler o gasto do dia.
W3_TIMES = os.environ.get("WORLDIN3_SCHEDULE_TIMES", "08:00,14:00,20:00")
W3_TZ = os.environ.get("WORLDIN3_SCHEDULE_TZ", "America/Sao_Paulo")
W3_BUDGET = int(os.environ.get("WORLDIN3_DAILY_CREDIT_BUDGET", "12000"))

# Containers que o painel espera ver "running" (o próprio ops-agent fica de fora
# de propósito — não faz parte do pipeline de vídeo).
EXPECTED_CONTAINERS = [
    c.strip()
    for c in os.environ.get(
        "EXPECTED_CONTAINERS",
        "redis,collector,classifier,commentator,promoter,synthesizer,"
        "renderer,streamer,metrics-api",
    ).split(",")
    if c.strip()
]

# --- Fase 2/4 — execução de comandos (docs/canal-monitor-plan.md) -----------
# Long-poll de comandos: cada tick do loop principal espera até esse tempo por
# 1 comando antes de mandar o heartbeat. Mantém o heartbeat em ~cadência de
# COMMANDS_POLL_TIMEOUT_SEC (bem próximo do REPORT_INTERVAL_SEC de antes).
COMMANDS_POLL_TIMEOUT_SEC = int(os.environ.get("OPS_AGENT_COMMANDS_POLL_TIMEOUT_SEC", "20"))
AGENT_ENABLED = os.environ.get("OPS_AGENT_EXECUTE_COMMANDS", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

RESTART_WHITELIST = [
    c.strip()
    for c in os.environ.get(
        "OPS_AGENT_RESTART_WHITELIST",
        "renderer,synthesizer,commentator,classifier,collector,promoter",
    ).split(",")
    if c.strip()
]
RESTART_STREAM_MAX_PER_HOUR = int(os.environ.get("OPS_AGENT_RESTART_STREAM_MAX_PER_HOUR", "4"))
RESTART_STREAM_MIN_GAP_SEC = int(os.environ.get("OPS_AGENT_RESTART_STREAM_MIN_GAP_SEC", "60"))
RESTART_SERVICE_MAX_PER_HOUR = int(os.environ.get("OPS_AGENT_RESTART_SERVICE_MAX_PER_HOUR", "4"))
INJECT_BREAKING_MAX_PER_HOUR = int(os.environ.get("OPS_AGENT_INJECT_BREAKING_MAX_PER_HOUR", "6"))
SET_ANCHOR_MAX_PER_HOUR = int(os.environ.get("OPS_AGENT_SET_ANCHOR_MAX_PER_HOUR", "12"))
BREAKING_TITLE_MAX_CHARS = 180
BREAKING_SUMMARY_MAX_CHARS = 600
# "avatar:provider_override" no Redis — mesma chave lida por renderer/main.py
# e synthesizer/main.py (_active_avatar_provider). Extensão nossa ao plano
# original (não estava no doc de 01/09): troca instantânea das 2 âncoras
# atuais, sem restart. "auto" remove o override (volta pro AVATAR_PROVIDER
# do .env).
AVATAR_OVERRIDE_KEY = "avatar:provider_override"
ANCHOR_MODE_TO_PROVIDER = {"feminina": "d-id", "fabio": "musetalk", "auto": None}

# stream -> consumer group (para medir backlog de consumidor preso).
STREAM_GROUPS = {
    "news.raw": "classifier-group",
    "news.classified": "commentator-group",
    "news.final": "synthesizer-group",
    "news.ready": "renderer-group",
}

TAG = "ops-agent"

r = redis_lib.Redis(
    host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_timeout=5
)
_dcli = None

# Janela em memória para "reinícios na última hora": name -> [(ts, RestartCount)].
# Zera se o próprio ops-agent reiniciar (aceitável na Fase 1).
_restart_history = {}


def log(*a):
    print(f"[{TAG}]", *a, flush=True)


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def docker_client():
    global _dcli
    if _dcli is None:
        if docker is None:
            raise RuntimeError("lib 'docker' não instalada na imagem")
        _dcli = docker.DockerClient(base_url="unix:///var/run/docker.sock")
    return _dcli


# --- Blocos do snapshot -------------------------------------------------------
def containers_block():
    """states nome->status, quais esperados faltam, e RestartCount por container."""
    try:
        cli = docker_client()
        found = cli.containers.list(
            all=True,
            filters={"label": f"com.docker.compose.project={COMPOSE_PROJECT}"},
        )
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "states": {}, "missing": EXPECTED_CONTAINERS}

    states, restart_counts, started_at = {}, {}, {}
    for c in found:
        states[c.name] = c.status
        st = c.attrs.get("State") or {}
        restart_counts[c.name] = c.attrs.get("RestartCount", 0)
        started_at[c.name] = st.get("StartedAt")
    missing = [n for n in EXPECTED_CONTAINERS if states.get(n) != "running"]
    return {
        "states": states,
        "missing": missing,
        "restart_counts": restart_counts,
        "started_at": started_at,
    }


def restarts_last_hour(name, current_count):
    """Delta de RestartCount na última hora, a partir da janela em memória."""
    hist = _restart_history.setdefault(name, [])
    now = time.time()
    hist.append((now, current_count))
    cutoff = now - 3600
    while hist and hist[0][0] < cutoff:
        hist.pop(0)
    if not hist:
        return 0
    return max(0, current_count - min(c for _, c in hist))


def uptime_seconds(started_at_iso):
    if not started_at_iso:
        return None
    try:
        s = started_at_iso.replace("Z", "+00:00")
        # Docker manda nanossegundos; datetime aceita até microssegundos.
        if "." in s:
            head, tail = s.split(".", 1)
            frac = ""
            tzpart = ""
            for i, ch in enumerate(tail):
                if ch in "+-":
                    frac, tzpart = tail[:i], tail[i:]
                    break
            else:
                frac = tail
            frac = (frac + "000000")[:6]
            s = f"{head}.{frac}{tzpart}"
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return int((now_utc() - dt).total_seconds())
    except Exception:
        return None


def final_mp4_block():
    try:
        st = os.stat(FINAL_MP4_PATH)
        return {
            "exists": True,
            "age_sec": int(time.time() - st.st_mtime),
            "size_bytes": st.st_size,
        }
    except FileNotFoundError:
        return {"exists": False, "age_sec": None, "size_bytes": 0}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def renderer_emission_block():
    try:
        ts = r.get("metrics:renderer:last_emission")
    except Exception as e:
        return {"last_emission": None, "age_sec": None, "error": str(e)}
    if not ts:
        return {"last_emission": None, "age_sec": None}
    try:
        dt = datetime.datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return {"last_emission": ts, "age_sec": int((now_utc() - dt).total_seconds())}
    except ValueError:
        return {"last_emission": ts, "age_sec": None}


def xpending_block():
    out = {}
    for stream, group in STREAM_GROUPS.items():
        try:
            info = r.xpending(stream, group)
            if isinstance(info, dict):
                out[group] = int(info.get("pending", 0) or 0)
            else:
                out[group] = int(info or 0)
        except Exception:
            out[group] = None
    return out


def worldin3_block():
    today = datetime.date.today().isoformat()
    used = 0
    try:
        v = r.get(f"worldin3:elevenlabs:credits_used:{today}")
        used = int(v) if v else 0
    except Exception:
        pass

    next_fire = None
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(W3_TZ)
            now_l = datetime.datetime.now(tz)
            slots = []
            for t in W3_TIMES.split(","):
                hh, mm = t.strip().split(":")
                slots.append((int(hh), int(mm)))
            cands = []
            for d in (0, 1):
                day = now_l.date() + datetime.timedelta(days=d)
                for hh, mm in slots:
                    cand = datetime.datetime(
                        day.year, day.month, day.day, hh, mm, tzinfo=tz
                    )
                    if cand > now_l:
                        cands.append(cand)
            if cands:
                next_fire = (
                    min(cands)
                    .astimezone(datetime.timezone.utc)
                    .isoformat(timespec="seconds")
                )
        except Exception as e:
            log("worldin3 próximo-disparo: erro no cálculo:", e)

    return {
        "next_scheduled_run": next_fire,
        "last_upload": None,  # ainda não persistido pelo scheduler.py
        "elevenlabs_used_today": used,
        "elevenlabs_budget": W3_BUDGET,
        "schedule_times": W3_TIMES,
        "schedule_tz": W3_TZ,
    }


def fetch_metrics():
    """Repassa o /status/all do metrics-api dentro do snapshot (best-effort).
    O dashboard também chama o metrics-api direto; isto é redundância barata e
    permite, no futuro, o painel depender só do agente."""
    if not METRICS_KEY:
        return {"error": "METRICS_API_KEY não configurada no ops-agent"}
    try:
        resp = requests.get(
            f"{METRICS_URL}/status/all",
            headers={"X-API-Key": METRICS_KEY},
            timeout=HTTP_TIMEOUT_SEC,
        )
    except requests.RequestException as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}", "body": resp.text[:300]}
    try:
        return resp.json()
    except ValueError:
        return {"error": "resposta não-JSON"}


def stream_block(containers):
    states = containers.get("states", {})
    running = states.get("streamer") == "running"
    fm = final_mp4_block()
    age = fm.get("age_sec")
    rc = containers.get("restart_counts", {}).get("streamer", 0)
    return {
        "streamer_running": running,
        "final_mp4": fm,
        "youtube_live": None,  # Fase 3: checagem real via YouTube Data API
        "live": bool(running and age is not None and age < STREAM_LIVE_MAX_AGE_SEC),
        "streamer_restarts_last_hour": restarts_last_hour("streamer", rc),
        "uptime_seconds": uptime_seconds(
            containers.get("started_at", {}).get("streamer")
        ),
    }


def build_snapshot():
    containers = containers_block()
    return {
        "generated_at": iso(now_utc()),
        "agent": {"phase": 1, "consumes_commands": False},
        "stream": stream_block(containers),
        "pipeline": {
            "containers": containers,
            "renderer_emission": renderer_emission_block(),
            "xpending": xpending_block(),
        },
        "worldin3": worldin3_block(),
        "watchdog": {
            "enabled": False,
            "last_action": None,
            "actions_last_hour": 0,
            "alerts": [],
        },
        "metrics": fetch_metrics(),
    }


def post_report(snapshot, command_result=None):
    body = {"heartbeatAt": iso(now_utc()), "snapshot": snapshot}
    if command_result:
        body["commandResult"] = command_result
    return requests.post(
        f"{APP_BASE_URL}/api/canal/agent/report",
        json=body,
        headers={"X-Canal-Agent-Secret": OPS_SECRET},
        timeout=HTTP_TIMEOUT_SEC,
    )


# --- Execução de comandos (whitelist FIXA, Fase 2/4) ------------------------
# Janelas em memória (zeram em restart do ops-agent — mesma limitação aceita
# pra `_restart_history`; auditoria de verdade fica no `canal_audit` do lado
# Replit, que sobrevive a restart daqui).
_action_history = {}  # chave da ação (ex.: "restart_stream", "restart:renderer") -> [timestamps]


def _rate_limited(key, max_per_hour, min_gap_sec=0):
    """True = BARRADO (excedeu o limite ou não passou o intervalo mínimo).
    Só registra o timestamp quando NÃO barrado (chamador só age se liberado)."""
    hist = _action_history.setdefault(key, [])
    now = time.time()
    cutoff = now - 3600
    while hist and hist[0] < cutoff:
        hist.pop(0)
    if hist and min_gap_sec and (now - hist[-1]) < min_gap_sec:
        return True
    if len(hist) >= max_per_hour:
        return True
    hist.append(now)
    return False


def _breaking_id(title):
    seed = f"breaking:{title}:{time.time()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _action_restart_stream(_params):
    if _rate_limited("restart_stream", RESTART_STREAM_MAX_PER_HOUR, RESTART_STREAM_MIN_GAP_SEC):
        return False, (
            f"recusado: < {RESTART_STREAM_MIN_GAP_SEC}s do último restart_stream "
            f"ou já bateu {RESTART_STREAM_MAX_PER_HOUR}/h"
        )
    docker_client().containers.get("streamer").restart(timeout=20)
    return True, "streamer reiniciado"


def _action_restart_service(params):
    name = str((params or {}).get("name", "")).strip()
    if name not in RESTART_WHITELIST:
        return False, f"recusado: {name!r} não está na whitelist {RESTART_WHITELIST}"
    if _rate_limited(f"restart:{name}", RESTART_SERVICE_MAX_PER_HOUR):
        return False, f"recusado: {name} já bateu {RESTART_SERVICE_MAX_PER_HOUR}/h"
    docker_client().containers.get(name).restart(timeout=20)
    return True, f"{name} reiniciado"


def _action_inject_breaking(params):
    title = str((params or {}).get("title", "")).strip()
    summary = str((params or {}).get("summary", "")).strip()
    if not title:
        return False, "recusado: title vazio"
    if len(title) > BREAKING_TITLE_MAX_CHARS:
        return False, f"recusado: title > {BREAKING_TITLE_MAX_CHARS} chars"
    if len(summary) > BREAKING_SUMMARY_MAX_CHARS:
        return False, f"recusado: summary > {BREAKING_SUMMARY_MAX_CHARS} chars"
    if _rate_limited("inject_breaking", INJECT_BREAKING_MAX_PER_HOUR):
        return False, f"recusado: já bateu {INJECT_BREAKING_MAX_PER_HOUR}/h"
    item_id = _breaking_id(title)
    item = {
        "id": item_id,
        "title": title,
        "summary": summary,
        "link": "",
        "published": iso(now_utc()),
        "source": "Redação",
        "priority": "breaking",
        "injected_by": (params or {}).get("requested_by", "painel"),
        "injected_at": iso(now_utc()),
    }
    r.xadd("news.raw", item)
    log(f"inject_breaking: {item_id} {title!r}")
    return True, f"publicado em news.raw ({item_id}) — fast-track no commentator"


def _action_set_anchor(params):
    """Extensão nossa (fora do plano de 01/09): troca instantânea entre as 2
    âncoras atuais via Redis, lida por renderer/main.py e synthesizer/main.py
    (_active_avatar_provider). Sem restart, sem custo."""
    if _rate_limited("set_anchor", SET_ANCHOR_MAX_PER_HOUR):
        return False, f"recusado: set_anchor já bateu {SET_ANCHOR_MAX_PER_HOUR}/h"
    mode = str((params or {}).get("mode", "")).strip().lower()
    if mode not in ANCHOR_MODE_TO_PROVIDER:
        return False, f"recusado: mode {mode!r} inválido (feminina|fabio|auto)"
    provider = ANCHOR_MODE_TO_PROVIDER[mode]
    if provider is None:
        r.delete(AVATAR_OVERRIDE_KEY)
        return True, "override removido — volta pro AVATAR_PROVIDER do .env"
    r.set(AVATAR_OVERRIDE_KEY, provider)
    return True, f"âncora ativa agora: {mode} ({provider})"


ACTIONS = {
    "restart_stream": _action_restart_stream,
    "restart_service": _action_restart_service,
    "inject_breaking": _action_inject_breaking,
    "set_anchor": _action_set_anchor,
}


def execute_command(cmd):
    """Executa 1 comando da whitelist fixa. Devolve o commandResult pro
    /api/canal/agent/report. Qualquer ação fora de ACTIONS ou qualquer
    exceção volta como ok=False — nunca propaga (o loop principal não pode
    morrer por causa de 1 comando ruim)."""
    cmd_id = cmd.get("id", "?")
    action = cmd.get("action", "")
    params = cmd.get("params") or {}
    fn = ACTIONS.get(action)
    if fn is None:
        ok, detail = False, f"ação desconhecida: {action!r} (whitelist: {list(ACTIONS)})"
    else:
        try:
            ok, detail = fn(params)
        except Exception as e:
            ok, detail = False, f"ERRO ao executar {action}: {type(e).__name__}: {e}"
    log(f"comando {cmd_id} action={action!r} ok={ok} detail={detail!r}")
    # "success" (não "ok") é o nome de campo que o backend Replit espera em
    # commandResult — ver artifacts/api-server/src/routes/canal.ts.
    return {"id": cmd_id, "success": ok, "detail": detail}


def long_poll_next_command():
    """GET /api/canal/agent/next-command — bloqueia no lado Replit até ~25s ou
    até haver 1 comando. {"command": null} no timeout é normal, não é erro."""
    resp = requests.get(
        f"{APP_BASE_URL}/api/canal/agent/next-command",
        headers={"X-Canal-Agent-Secret": OPS_SECRET},
        timeout=COMMANDS_POLL_TIMEOUT_SEC + 10,
        params={"timeout": COMMANDS_POLL_TIMEOUT_SEC},
    )
    resp.raise_for_status()
    return (resp.json() or {}).get("command")


def main():
    log(
        f"iniciando — app={APP_BASE_URL} metrics={METRICS_URL} "
        f"executa_comandos={AGENT_ENABLED} poll={COMMANDS_POLL_TIMEOUT_SEC}s "
        f"(FASE 2/4: comandos + inject_breaking + set_anchor)"
    )
    while True:
        t0 = time.time()
        command_result = None
        try:
            if AGENT_ENABLED:
                try:
                    cmd = long_poll_next_command()
                except requests.RequestException as e:
                    cmd = None
                    log("long-poll de comandos falhou (segue pro heartbeat):", e)
                if cmd:
                    command_result = execute_command(cmd)

            snap = build_snapshot()
            snap["agent"] = {"phase": 4, "consumes_commands": AGENT_ENABLED}
            resp = post_report(snap, command_result)
            if resp.status_code == 200:
                st = snap["stream"]
                pl = snap["pipeline"]["containers"]
                log(
                    f"report OK — live={st['live']} "
                    f"streamer_up={st['streamer_running']} "
                    f"missing={pl.get('missing')} "
                    f"render_age={snap['pipeline']['renderer_emission'].get('age_sec')}s"
                )
            else:
                log(f"report HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            log("ciclo ERRO:", type(e).__name__, e)
            traceback.print_exc()
        elapsed = time.time() - t0
        # Se o long-poll já consumiu o intervalo (caso comum: comandos/next
        # bloqueia até COMMANDS_POLL_TIMEOUT_SEC), não dorme de novo.
        time.sleep(max(0.0, min(REPORT_INTERVAL_SEC, COMMANDS_POLL_TIMEOUT_SEC) - elapsed))


if __name__ == "__main__":
    main()
