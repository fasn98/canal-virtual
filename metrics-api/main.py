"""
metrics-api — API leve, SOMENTE LEITURA, com métricas de uso/custo do pipeline.

Serve para alimentar um painel de controle (back-office) hospedado à parte
(Replit). Este serviço só PRODUZ os dados; nenhuma composição de vídeo, nenhuma
escrita no pipeline. Todas as rotas são GET.

Fontes dos dados (nada é inventado aqui — só leitura):
  - Redis: contador de créditos da ElevenLabs (`elevenlabs:credits_used:<data>`,
    escrito pelo synthesizer) e os contadores `metrics:*:<data>` que os serviços
    do pipeline incrementam (chamadas ao Claude, chamadas ao D-ID, cache
    hit/miss de comentário e de áudio, timestamp da última emissão do renderer).
  - API do D-ID: saldo de créditos real via GET /credits (quando DID_API_KEY
    está configurada).
  - Docker Engine (socket montado :ro): quais containers estão up.

Segurança: header "X-API-Key" comparado (constant-time) com METRICS_API_KEY.
Sem o header correto TODA rota de dados responde 401 — nunca os dados.
"""

import base64
import datetime
import hmac
import os

import redis
import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

# --- Config (tudo via env, sem rebuild) ------------------------------------
METRICS_API_KEY = os.environ.get("METRICS_API_KEY", "").strip()

# Mesmo teto lido pelo synthesizer; usado só para calcular "restante".
DAILY_CREDIT_BUDGET = int(os.environ.get("DAILY_CREDIT_BUDGET", "4000"))

ENABLE_LIPSYNC = os.environ.get("ENABLE_LIPSYNC", "false").strip().lower() in (
    "1", "true", "yes", "on",
)
DID_API_KEY = os.environ.get("DID_API_KEY", "").strip()
DID_API_URL = os.environ.get("DID_API_URL", "https://api.d-id.com").rstrip("/")
DID_HTTP_TIMEOUT_SEC = float(os.environ.get("DID_HTTP_TIMEOUT_SEC", "15"))

REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

# Containers que o painel espera ver "running" (para marcar o que falta).
EXPECTED_CONTAINERS = [
    c.strip()
    for c in os.environ.get(
        "EXPECTED_CONTAINERS",
        "redis,collector,classifier,commentator,promoter,synthesizer,"
        "renderer,streamer,metrics-api",
    ).split(",")
    if c.strip()
]

# Tipos de chamada ao Claude contabilizados pelo pipeline (commentator + os
# três revisores). As chaves são `metrics:claude_calls:<tipo>:<data>`.
CLAUDE_CALL_KINDS = ["commentary", "fact_check", "editorial", "final_approval"]

r = redis.Redis(
    host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_timeout=5
)

app = FastAPI(
    title="canal-virtual metrics-api",
    description="Métricas de uso/custo do pipeline (somente leitura).",
    version="1.0.0",
)


# --- Autenticação por token fixo -----------------------------------------
def require_key(x_api_key: str = Header(default="", alias="X-API-Key")):
    """Barreira única de todas as rotas de dados. Comparação constant-time.
    Sem METRICS_API_KEY configurada, NINGUÉM entra (fail-closed)."""
    if not METRICS_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="METRICS_API_KEY não configurada no servidor.",
        )
    if not x_api_key or not hmac.compare_digest(x_api_key, METRICS_API_KEY):
        raise HTTPException(status_code=401, detail="X-API-Key ausente ou inválida.")


# --- Helpers de leitura -------------------------------------------------
def _today():
    return datetime.date.today().isoformat()


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _get_int(key):
    """Lê um contador inteiro do Redis (0 se ausente ou ilegível)."""
    try:
        v = r.get(key)
        return int(v) if v not in (None, "") else 0
    except Exception:
        return 0


def _cache_rate(domain):
    """hit/miss/total/hit_rate de hoje para um domínio de cache ('commentary'
    ou 'audio'). hit_rate = None quando ainda não houve nenhum evento hoje."""
    today = _today()
    hit = _get_int(f"metrics:cache:{domain}:hit:{today}")
    miss = _get_int(f"metrics:cache:{domain}:miss:{today}")
    total = hit + miss
    return {
        "hit": hit,
        "miss": miss,
        "total": total,
        "hit_rate": round(hit / total, 4) if total else None,
    }


# --- Blocos de métrica (reaproveitados por /status/all) ----------------
def metric_elevenlabs():
    """Orçamento diário de créditos da ElevenLabs. `used` vem do contador que o
    synthesizer incrementa a cada síntese bem-sucedida."""
    today = _today()
    used = _get_int(f"elevenlabs:credits_used:{today}")
    remaining = DAILY_CREDIT_BUDGET - used
    return {
        "date": today,
        "budget": DAILY_CREDIT_BUDGET,
        "used": used,
        "remaining": remaining,
        "pct_used": round(100 * used / DAILY_CREDIT_BUDGET, 1)
        if DAILY_CREDIT_BUDGET
        else None,
        "exceeded": used >= DAILY_CREDIT_BUDGET,
    }


def _did_auth_header():
    key = DID_API_KEY
    if ":" in key:  # "usuario:senha" -> base64 (mesmo tratamento do renderer)
        key = base64.b64encode(key.encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {key}", "accept": "application/json"}


def _did_credits():
    """Consulta GET /credits na API do D-ID. Retorna um dict com
    remaining/total quando dá para extrair, ou {"error": ...} em qualquer
    falha (rede, auth, formato inesperado). Nunca levanta."""
    if not DID_API_KEY:
        return {"error": "DID_API_KEY não configurada"}
    try:
        resp = requests.get(
            f"{DID_API_URL}/credits",
            headers=_did_auth_header(),
            timeout=DID_HTTP_TIMEOUT_SEC,
        )
    except requests.RequestException as e:
        return {"error": f"falha de conexão: {type(e).__name__}: {e}"}
    if resp.status_code != 200:
        return {
            "error": f"HTTP {resp.status_code}",
            "body": resp.text[:300],
        }
    try:
        data = resp.json()
    except ValueError:
        return {"error": "resposta não-JSON", "body": resp.text[:300]}

    # A resposta do D-ID traz uma lista "credits" (uma entrada por pacote de
    # créditos) e, dependendo da conta, campos agregados no topo. Somamos o que
    # der e devolvemos o payload cru para o painel inspecionar.
    remaining = data.get("remaining")
    total = data.get("total")
    expire_at = data.get("expire_at") or data.get("expires_at")
    items = data.get("credits")
    if isinstance(items, list) and items:
        def _sum(field):
            vals = [it.get(field) for it in items if isinstance(it.get(field), (int, float))]
            return sum(vals) if vals else None

        remaining = _sum("remaining") if remaining is None else remaining
        total = _sum("total") if total is None else total
        if expire_at is None:
            expire_at = items[0].get("expire_at") or items[0].get("expires_at")

    return {
        "remaining": remaining,
        "total": total,
        "expire_at": expire_at,
        "raw": data,
    }


def metric_did():
    """Estado do lip-sync D-ID: se está ligado, saldo real de créditos (quando
    a chave está configurada) e a contagem de chamadas de hoje (sucesso/falha),
    que o renderer/lipsync.py registra."""
    today = _today()
    return {
        "date": today,
        "enable_lipsync": ENABLE_LIPSYNC,
        "credits": _did_credits(),
        "calls_today": {
            "success": _get_int(f"metrics:did_calls:success:{today}"),
            "fail": _get_int(f"metrics:did_calls:fail:{today}"),
        },
    }


def metric_claude():
    """Chamadas ao Claude hoje, por tipo (commentator + os 3 revisores)."""
    today = _today()
    by_type = {
        kind: _get_int(f"metrics:claude_calls:{kind}:{today}")
        for kind in CLAUDE_CALL_KINDS
    }
    return {
        "date": today,
        "by_type": by_type,
        "total": sum(by_type.values()),
    }


def _containers_status():
    """Mapa nome->estado dos containers deste projeto compose, via socket do
    Docker montado :ro. Retorna {"error": ...} se o socket não estiver
    acessível. Containers fora do projeto (ex.: outros stacks na mesma VM)
    são ignorados; se não der para descobrir o projeto, lista todos."""
    try:
        import docker
        import socket

        client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        try:
            # Descobre o projeto compose a partir do próprio container (hostname
            # = id curto). Se falhar, cai para "sem filtro".
            project = None
            try:
                me = client.containers.get(socket.gethostname())
                project = (me.labels or {}).get("com.docker.compose.project")
            except Exception:
                project = None

            filters = {"label": f"com.docker.compose.project={project}"} if project else {}
            running, all_states = {}, {}
            for c in client.containers.list(all=True, filters=filters):
                state = c.status  # running | exited | created | paused | ...
                all_states[c.name] = state
                if state == "running":
                    health = (
                        (c.attrs.get("State") or {}).get("Health") or {}
                    ).get("Status")
                    running[c.name] = health or "running"
            missing = [
                name for name in EXPECTED_CONTAINERS if all_states.get(name) != "running"
            ]
            return {
                "project": project,
                "states": all_states,
                "running": running,
                "missing_expected": missing,
            }
        finally:
            client.close()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def metric_pipeline():
    """Saúde geral: containers up, última emissão do renderer e taxa de cache
    (comentário e áudio) de hoje."""
    last_emission = None
    try:
        last_emission = r.get("metrics:renderer:last_emission")
    except Exception:
        last_emission = None

    age_sec = None
    if last_emission:
        try:
            dt = datetime.datetime.fromisoformat(last_emission)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            age_sec = int(
                (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()
            )
        except ValueError:
            age_sec = None

    return {
        "date": _today(),
        "containers": _containers_status(),
        "renderer_last_emission": last_emission,
        "renderer_last_emission_age_sec": age_sec,
        "cache": {
            "commentary": _cache_rate("commentary"),
            "audio": _cache_rate("audio"),
        },
    }


# --- Rotas -------------------------------------------------------------
@app.get("/health")
def health():
    """Liveness SEM token e SEM dados — só para uptime check do painel."""
    return {"service": "metrics-api", "ok": True, "time": _now_iso()}


@app.get("/status/elevenlabs", dependencies=[Depends(require_key)])
def status_elevenlabs():
    return metric_elevenlabs()


@app.get("/status/did", dependencies=[Depends(require_key)])
def status_did():
    return metric_did()


@app.get("/status/claude", dependencies=[Depends(require_key)])
def status_claude():
    return metric_claude()


@app.get("/status/pipeline", dependencies=[Depends(require_key)])
def status_pipeline():
    return metric_pipeline()


@app.get("/status/all", dependencies=[Depends(require_key)])
def status_all():
    """Tudo num JSON só. Cada bloco é isolado: se um subsistema falhar, os
    outros ainda voltam — o bloco problemático traz sua própria chave "error"."""
    blocks = {}
    for name, fn in (
        ("elevenlabs", metric_elevenlabs),
        ("did", metric_did),
        ("claude", metric_claude),
        ("pipeline", metric_pipeline),
    ):
        try:
            blocks[name] = fn()
        except Exception as e:
            blocks[name] = {"error": f"{type(e).__name__}: {e}"}
    return {"generated_at": _now_iso(), **blocks}


@app.exception_handler(HTTPException)
def _http_exc(_request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
