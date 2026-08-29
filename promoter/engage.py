"""
Bot de engajamento — 1 pergunta por BLOCO no chat ao vivo do YouTube.

Chamado pelo promoter no fim de cada ciclo (junto com a chamada promocional,
alinhado ao ciclo de NEWS_PER_PROMO notícias). Gera via Claude uma pergunta
curta em português sobre a notícia que fechou o bloco (a mais recente — "a
notícia do momento") e posta no chat ao vivo via liveChatMessages.insert,
simulando enquete/quiz.

A API nativa de Polls do YouTube não tem automação confiável; por isso o
caminho é mensagem de texto no chat.

TUDO aqui é best-effort. `maybe_post_block_question()` captura QUALQUER exceção
e apenas loga — NUNCA propaga para o promoter, então o pipeline de vídeo segue
intacto mesmo com token expirado, chat fora do ar ou rate limit.

LIGA/DESLIGA (tudo via env, sem rebuild além do 1º)
  ENABLE_ENGAGEMENT_BOT      padrão "false"
  YOUTUBE_OAUTH_CLIENT_ID / _CLIENT_SECRET / _REFRESH_TOKEN   obrigatórios
  YOUTUBE_LIVE_VIDEO_ID      opcional; vazio => descobre a live ativa do canal
  ENGAGEMENT_MODEL           opcional; vazio => ANTHROPIC_MODEL
  ENGAGEMENT_CHATID_TTL_SEC  opcional; cache do liveChatId no Redis (padrão 1800)

Escopo OAuth necessário: https://www.googleapis.com/auth/youtube.force-ssl
(cobre liveChatMessages.insert, liveBroadcasts.list e videos.list).

Cota YouTube: insert custa 50 unidades; videos.list / liveBroadcasts.list 1
unidade. ~1 chamada por bloco (~11 min) => folga enorme nos 10.000/dia.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

TAG = "Engage"

ENABLED = os.environ.get("ENABLE_ENGAGEMENT_BOT", "false").strip().lower() in (
    "1", "true", "yes", "on",
)

CLIENT_ID = os.environ.get("YOUTUBE_OAUTH_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET", "").strip()
REFRESH_TOKEN = os.environ.get("YOUTUBE_OAUTH_REFRESH_TOKEN", "").strip()
LIVE_VIDEO_ID = os.environ.get("YOUTUBE_LIVE_VIDEO_ID", "").strip()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_WORKSPACE_ID = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
ENGAGEMENT_MODEL = (
    os.environ.get("ENGAGEMENT_MODEL", "").strip()
    or os.environ.get("ANTHROPIC_MODEL", "").strip()
    or "claude-sonnet-4-5"
)
ANTHROPIC_TIMEOUT_SEC = float(os.environ.get("ANTHROPIC_TIMEOUT_SEC", "30"))

TOKEN_URI = "https://oauth2.googleapis.com/token"
YT_API = "https://www.googleapis.com/youtube/v3"
HTTP_TIMEOUT = float(os.environ.get("ENGAGEMENT_HTTP_TIMEOUT_SEC", "20"))

# Limite de caracteres de uma mensagem no chat ao vivo do YouTube.
MAX_CHAT_CHARS = 200

# Chaves no Redis (reaproveitadas entre blocos / restarts do promoter).
CHATID_KEY = "engage:live_chat_id"
CHATID_TTL = int(os.environ.get("ENGAGEMENT_CHATID_TTL_SEC", "1800"))
ACCESS_KEY = "engage:access_token"

_anthropic_client = None
_creds_warned = False


# ---------------------------------------------------------------------------
# HTTP helpers (biblioteca padrão — sem dependência extra no promoter)
# ---------------------------------------------------------------------------
def _http(method, url, body=None, token=None, headers=None):
    """Retorna (status:int, data:dict). status=0 em erro de rede/timeout."""
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": {"errors": [{"reason": type(e).__name__}], "message": str(e)}}


def _reason(data):
    try:
        return data["error"]["errors"][0]["reason"]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# OAuth: access token a partir do refresh token (com cache no Redis)
# ---------------------------------------------------------------------------
def _access_token(r, force_refresh=False):
    if not force_refresh:
        try:
            cached = r.get(ACCESS_KEY)
            if cached:
                return cached
        except Exception:
            pass

    body = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).encode()
    st, data = _http("POST", TOKEN_URI, body=body)
    if st != 200 or "access_token" not in data:
        raise RuntimeError(f"refresh do token OAuth falhou: HTTP {st} {str(data)[:200]}")

    at = data["access_token"]
    ttl = max(60, int(data.get("expires_in", 3600)) - 60)
    try:
        r.set(ACCESS_KEY, at, ex=ttl)
    except Exception:
        pass
    return at


# ---------------------------------------------------------------------------
# Descoberta do liveChatId da transmissão ativa
# ---------------------------------------------------------------------------
def _discover_live_chat_id(access_token):
    """1) videos.list se YOUTUBE_LIVE_VIDEO_ID setado; 2) liveBroadcasts.list
    da live ativa do canal. Retorna o id ou None."""
    if LIVE_VIDEO_ID:
        st, data = _http(
            "GET",
            f"{YT_API}/videos?part=liveStreamingDetails&id={urllib.parse.quote(LIVE_VIDEO_ID)}",
            token=access_token,
        )
        items = data.get("items", []) if st == 200 else []
        if items:
            cid = items[0].get("liveStreamingDetails", {}).get("activeLiveChatId")
            if cid:
                return cid
        print(
            f"{TAG} → videos.list não trouxe activeLiveChatId p/ "
            f"{LIVE_VIDEO_ID!r} (HTTP {st}); tentando liveBroadcasts.",
            flush=True,
        )

    st, data = _http(
        "GET",
        f"{YT_API}/liveBroadcasts?part=snippet&broadcastStatus=active"
        "&broadcastType=all&maxResults=5",
        token=access_token,
    )
    if st != 200:
        print(f"{TAG} → liveBroadcasts.list HTTP {st}: {str(data)[:200]}", flush=True)
        return None
    for it in data.get("items", []):
        cid = it.get("snippet", {}).get("liveChatId")
        if cid:
            return cid
    return None


def _get_live_chat_id(r, access_token):
    """(id, from_cache). Cacheia no Redis com TTL — a live 24h pode reiniciar
    e trocar o liveChatId."""
    try:
        cached = r.get(CHATID_KEY)
        if cached:
            return cached, True
    except Exception:
        pass

    cid = _discover_live_chat_id(access_token)
    if cid:
        try:
            r.set(CHATID_KEY, cid, ex=CHATID_TTL)
        except Exception:
            pass
    return cid, False


# ---------------------------------------------------------------------------
# Geração da pergunta (Claude, com fallback template)
# ---------------------------------------------------------------------------
def _get_anthropic():
    global _anthropic_client
    if not ANTHROPIC_API_KEY:
        return None
    if _anthropic_client is None:
        import anthropic
        headers = {}
        if ANTHROPIC_WORKSPACE_ID:
            headers["anthropic-workspace-id"] = ANTHROPIC_WORKSPACE_ID
        _anthropic_client = anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY,
            timeout=ANTHROPIC_TIMEOUT_SEC,
            max_retries=1,
            default_headers=headers or None,
        )
    return _anthropic_client


def _fallback_question(title, category):
    t = " ".join((title or "a notícia do momento").split())
    if len(t) > 130:
        t = t[:127].rstrip() + "…"
    return f"E aí, o que vocês acham? “{t}” Comenta aqui 👇"


def _build_prompt(title, category, summary):
    resumo = " ".join((summary or "").split())[:600] or "(sem resumo)"
    return (
        "Você é o community manager de um telejornal 24h ao vivo no YouTube. "
        "Escreva UMA pergunta curta e direta, em português do Brasil, para jogar "
        "no chat ao vivo e puxar conversa sobre a notícia abaixo. Estilo "
        "enquete/quiz informal, que convida o espectador a responder.\n\n"
        f"TÍTULO: {title}\n"
        f"CATEGORIA: {category}\n"
        f"RESUMO: {resumo}\n\n"
        "Regras:\n"
        "- Uma frase só, no máximo 160 caracteres.\n"
        "- Termine com uma chamada para ação (ex.: \"comenta aqui 👇\", "
        "\"vota aí\") e no máximo um emoji.\n"
        "- Não invente fatos além do título/resumo. Não tome partido.\n"
        "- Sem aspas, sem hashtags, sem markdown. Responda só com a pergunta."
    )


def _make_question(title, category, summary):
    client = _get_anthropic()
    if client is None:
        print(f"{TAG} → ANTHROPIC_API_KEY ausente; pergunta template.", flush=True)
        return _fallback_question(title, category)
    try:
        resp = client.messages.create(
            model=ENGAGEMENT_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": _build_prompt(title, category, summary)}],
        )
        text = " ".join(
            b.text.strip() for b in resp.content
            if getattr(b, "type", None) == "text" and b.text.strip()
        ).strip().strip('"').strip("“”").strip()
        if not text:
            raise RuntimeError("resposta vazia da API")
        print(f"{TAG} → pergunta gerada via Claude ({ENGAGEMENT_MODEL}).", flush=True)
        return text
    except Exception as e:
        print(
            f"{TAG} → Claude falhou ({type(e).__name__}: {e}); pergunta template.",
            flush=True,
        )
        return _fallback_question(title, category)


def _clamp(text):
    text = " ".join((text or "").split())
    if len(text) <= MAX_CHAT_CHARS:
        return text
    return text[: MAX_CHAT_CHARS - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Post no chat
# ---------------------------------------------------------------------------
def _post_message(access_token, live_chat_id, text):
    payload = json.dumps({
        "snippet": {
            "liveChatId": live_chat_id,
            "type": "textMessageEvent",
            "textMessageDetails": {"messageText": text},
        }
    }).encode()
    return _http(
        "POST",
        f"{YT_API}/liveChat/messages?part=snippet",
        body=payload,
        token=access_token,
        headers={"Content-Type": "application/json"},
    )


def _run(r, news_id, title, category, summary):
    if not (CLIENT_ID and CLIENT_SECRET and REFRESH_TOKEN):
        global _creds_warned
        if not _creds_warned:
            print(
                f"{TAG} → credenciais YOUTUBE_OAUTH_* incompletas; bot inativo.",
                flush=True,
            )
            _creds_warned = True
        return False

    question = _clamp(_make_question(title, category, summary))
    access_token = _access_token(r)

    live_chat_id, from_cache = _get_live_chat_id(r, access_token)
    if not live_chat_id:
        print(f"{TAG} → nenhuma live ativa / liveChatId não encontrado; pulo o bloco.", flush=True)
        return False

    st, data = _post_message(access_token, live_chat_id, question)

    # Token venceu no meio do bloco -> renova 1x e repete.
    if st == 401:
        access_token = _access_token(r, force_refresh=True)
        st, data = _post_message(access_token, live_chat_id, question)

    # liveChatId velho (a live reiniciou) -> limpa cache, redescobre 1x.
    if st in (403, 404) and _reason(data) in (
        "liveChatNotFound", "liveChatEnded", "liveChatDisabled",
    ):
        try:
            r.delete(CHATID_KEY)
        except Exception:
            pass
        if from_cache:
            live_chat_id, _ = _get_live_chat_id(r, access_token)
            if live_chat_id:
                st, data = _post_message(access_token, live_chat_id, question)

    if st == 200:
        print(f"{TAG} → pergunta postada no chat ({data.get('id')}): {question!r}", flush=True)
        return True

    if st == 429 or _reason(data) == "rateLimitExceeded":
        print(f"{TAG} → rate limit do chat ao vivo; pulo este bloco.", flush=True)
        return False

    print(
        f"{TAG} → FALHA ao postar (HTTP {st}, motivo={_reason(data)!r}): {str(data)[:300]}",
        flush=True,
    )
    return False


def maybe_post_block_question(r, news_id="", title="", category="", summary=""):
    """Ponto de entrada único chamado pelo promoter. Best-effort: engole
    qualquer exceção e apenas loga. Retorna True só se a mensagem foi postada."""
    if not ENABLED:
        return False
    try:
        return _run(r, news_id, title, category, summary)
    except Exception as e:
        print(
            f"{TAG} → erro não fatal ({type(e).__name__}: {e}); pipeline segue normal.",
            flush=True,
        )
        return False
