"""
Integração OPCIONAL com o D-ID para lip-sync real do avatar da apresentadora.

Prova de conceito (plano Pro, ~1 mês). Todo o acoplamento com o D-ID vive
neste módulo — o renderer só chama `get_lipsync_video()` e, se receber None,
segue com o avatar estático exatamente como antes.

LIGA/DESLIGA
------------
Controlado por ENABLE_LIPSYNC (env, padrão "false"). Com ENABLE_LIPSYNC=false
o renderer nem importa este módulo no caminho principal: o pipeline roda
idêntico ao de hoje. Para desativar de vez basta manter a env em false —
nada precisa ser reescrito.

FLUXO (ENABLE_LIPSYNC=true), por notícia:
  1. cache: se assets/lipsync/{id}.mp4 já existe e > 0 bytes, reusa (0 créditos).
  2. upload do mp3 que a ElevenLabs já gerou   -> POST /audios  (multipart)
  3. upload da foto em chroma key (avatar_greenscreen.png) -> POST /images
     (multipart). O verde é removido no renderer, antes da composição.
  4. cria o "talk"                               -> POST /talks
       source_url = imagem, script = {type: "audio", audio_url: <mp3>}
  5. polling                                     -> GET /talks/{id} até status "done"
  6. baixa result_url para assets/lipsync/{id}.mp4

Qualquer falha (rede, cota, timeout, resposta inesperada) -> retorna None e o
renderer cai no avatar estático — mesmo padrão do fallback da ElevenLabs.

Referência da API (docs.d-id.com, conferida em 2026-08):
  - Base: https://api.d-id.com
  - Auth: header "Authorization: Basic <credencial>". A chave gerada no D-ID
    Studio vem como "API_USERNAME:API_PASSWORD"; se tiver ":", este módulo
    faz o base64 automaticamente. Se já vier em base64, é usada como está.
  - POST /audios  : multipart/form-data, campo "audio". Resp. 201 -> {"url": "..."}.
                    Armazenado ~24-48h. Aceita mp3 (convertido p/ wav 16kHz).
  - POST /images  : multipart/form-data, campo "image". Resp. 201 -> {"url": "..."}.
  - POST /talks   : JSON. Resp. -> {"id": "...", "status": "created"}.
  - GET  /talks/{id} : {"status": "created|started|done|error", "result_url": "...",
                        "duration": <seg>}. result_url é um link S3 temporário.
"""

import base64
import datetime
import os
import time

import redis
import requests

TAG = "D-ID"

# --- Contador para a metrics-api (best-effort) ----------------------------
# Cada chamada REAL ao D-ID (não cache) incrementa
# `metrics:did_calls:success:<data>` ou `:fail:<data>`, lido pela metrics-api
# (GET /status/did). Redis próprio; qualquer falha aqui é ignorada.
_metrics_r = redis.Redis(
    host="redis", port=6379, decode_responses=True, socket_timeout=5
)
_METRICS_TTL_SEC = 8 * 24 * 3600


def _bump_metric(field, n=1):
    try:
        key = f"metrics:{field}:{datetime.date.today().isoformat()}"
        _metrics_r.incrby(key, n)
        _metrics_r.expire(key, _METRICS_TTL_SEC)
    except Exception:
        pass

# --- Configuração (tudo via env, sem rebuild) ---
ENABLE_LIPSYNC = os.environ.get("ENABLE_LIPSYNC", "false").strip().lower() in (
    "1", "true", "yes", "on",
)
DID_API_KEY = os.environ.get("DID_API_KEY", "").strip()
DID_API_URL = os.environ.get("DID_API_URL", "https://api.d-id.com").rstrip("/")

# Foto usada pelo D-ID para detectar o rosto e animar. Precisa ser OPACA (mp4
# não tem transparência) — usamos a versão em CHROMA KEY (avatar_greenscreen.png):
# o recorte da apresentadora sobre fundo verde sólido. O renderer remove o verde
# com colorkey/chromakey do ffmpeg antes de compor, recuperando o efeito do
# recorte transparente sem o "retângulo flutuante" do fundo original da foto.
DID_SOURCE_IMAGE = os.environ.get(
    "DID_SOURCE_IMAGE", "/app/assets/avatar_greenscreen.png"
)

# Timeouts.
DID_HTTP_TIMEOUT_SEC = float(os.environ.get("DID_HTTP_TIMEOUT_SEC", "30"))
DID_POLL_INTERVAL_SEC = float(os.environ.get("DID_POLL_INTERVAL_SEC", "3"))
DID_POLL_TIMEOUT_SEC = float(os.environ.get("DID_POLL_TIMEOUT_SEC", "240"))

# stitch=true: cola o rosto animado de volta na foto original inteira (melhor
# quando a imagem não é um close perfeito). result_format=mp4 para o ffmpeg.
DID_STITCH = os.environ.get("DID_STITCH", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

LIPSYNC_DIR = os.environ.get("LIPSYNC_DIR", "/app/assets/lipsync")
os.makedirs(LIPSYNC_DIR, exist_ok=True)


def _auth_header():
    key = DID_API_KEY
    # Chave "usuario:senha" do Studio -> base64. Se já vier codificada (sem ":"),
    # usa como está.
    if ":" in key:
        key = base64.b64encode(key.encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {key}"}


def _upload(kind, field, path, content_type):
    """POST /audios ou /images (multipart). Retorna a URL temporária do D-ID."""
    url = f"{DID_API_URL}/{kind}"
    with open(path, "rb") as fh:
        files = {field: (os.path.basename(path), fh, content_type)}
        resp = requests.post(
            url, headers=_auth_header(), files=files, timeout=DID_HTTP_TIMEOUT_SEC
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"POST /{kind} status={resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    out = data.get("url") or data.get("audio_url")
    if not out:
        raise RuntimeError(f"POST /{kind} sem 'url' na resposta: {data}")
    return out


def _create_talk(image_url, audio_url):
    payload = {
        "source_url": image_url,
        "script": {"type": "audio", "audio_url": audio_url},
        "config": {"stitch": DID_STITCH, "result_format": "mp4"},
    }
    resp = requests.post(
        f"{DID_API_URL}/talks",
        headers={**_auth_header(), "Content-Type": "application/json"},
        json=payload,
        timeout=DID_HTTP_TIMEOUT_SEC,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"POST /talks status={resp.status_code}: {resp.text[:300]}")
    talk_id = resp.json().get("id")
    if not talk_id:
        raise RuntimeError(f"POST /talks sem 'id': {resp.json()}")
    return talk_id


def _poll_talk(talk_id):
    """GET /talks/{id} até status 'done'. Retorna (result_url, duração_seg)."""
    deadline = time.monotonic() + DID_POLL_TIMEOUT_SEC
    while time.monotonic() < deadline:
        resp = requests.get(
            f"{DID_API_URL}/talks/{talk_id}",
            headers=_auth_header(),
            timeout=DID_HTTP_TIMEOUT_SEC,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"GET /talks/{talk_id} status={resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        status = data.get("status")
        if status == "done":
            result_url = data.get("result_url")
            if not result_url:
                raise RuntimeError(f"talk {talk_id} 'done' sem result_url: {data}")
            return result_url, data.get("duration")
        if status in ("error", "rejected"):
            raise RuntimeError(f"talk {talk_id} status={status}: {data.get('error') or data}")
        time.sleep(DID_POLL_INTERVAL_SEC)
    raise TimeoutError(f"talk {talk_id} não concluiu em {DID_POLL_TIMEOUT_SEC:.0f}s")


def _download(result_url, out_path):
    resp = requests.get(result_url, timeout=DID_HTTP_TIMEOUT_SEC)
    if resp.status_code != 200 or not resp.content:
        raise RuntimeError(f"download do result_url falhou (status={resp.status_code}, bytes={len(resp.content)})")
    tmp = out_path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(resp.content)
    if os.path.getsize(tmp) == 0:
        os.remove(tmp)
        raise RuntimeError("vídeo baixado vazio")
    os.replace(tmp, out_path)


def get_lipsync_video(news_id, audio_path):
    """
    Retorna o caminho de um mp4 com lip-sync para esta notícia, ou None para
    o renderer usar o avatar estático (comportamento atual).

    None é retornado — sem levantar exceção — quando:
      - ENABLE_LIPSYNC=false;
      - DID_API_KEY não configurada;
      - o áudio real (mp3) não existe;
      - qualquer falha na chamada ao D-ID (rede, cota, timeout, etc).
    """
    if not ENABLE_LIPSYNC:
        return None

    out_path = os.path.join(LIPSYNC_DIR, f"{news_id}.mp4")

    # Cache por id (mesmo racional do cache de áudio): vídeo de notícia antiga
    # não muda; evita gastar créditos D-ID reprocessando o mesmo id.
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        print(f"{TAG} → Cache HIT para {news_id}, reaproveitando video de lip-sync existente", flush=True)
        return out_path

    if not DID_API_KEY:
        print(f"{TAG} → DID_API_KEY não configurada; usando avatar estático.", flush=True)
        return None
    if not audio_path or not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        print(f"{TAG} → áudio real ausente ({audio_path!r}) para {news_id}; usando avatar estático.", flush=True)
        return None
    if not os.path.exists(DID_SOURCE_IMAGE):
        print(f"{TAG} → imagem-fonte {DID_SOURCE_IMAGE!r} não encontrada; usando avatar estático.", flush=True)
        return None

    started = time.monotonic()
    try:
        print(f"{TAG} → Solicitando lip-sync para {news_id} (áudio={audio_path})", flush=True)
        audio_url = _upload("audios", "audio", audio_path, "audio/mpeg")
        image_url = _upload("images", "image", DID_SOURCE_IMAGE, "image/png")
        talk_id = _create_talk(image_url, audio_url)
        result_url, duration = _poll_talk(talk_id)
        _download(result_url, out_path)
        _bump_metric("did_calls:success")
        dur_txt = f"{float(duration):.1f}" if duration is not None else "?"
        print(
            f"{TAG} → Vídeo com lip-sync gerado para {news_id}, duração {dur_txt} segundos "
            f"(talk={talk_id}, {time.monotonic() - started:.1f}s de ponta a ponta) -> {out_path}",
            flush=True,
        )
        return out_path
    except Exception as e:
        _bump_metric("did_calls:fail")
        # Limpa qualquer mp4 parcial para não envenenar o cache.
        for p in (out_path, out_path + ".part"):
            try:
                if os.path.exists(p) and os.path.getsize(p) == 0:
                    os.remove(p)
            except OSError:
                pass
        print(
            f"{TAG} → FALHA ({type(e).__name__}: {e}) para {news_id}; "
            "usando avatar estático (fallback).",
            flush=True,
        )
        return None
