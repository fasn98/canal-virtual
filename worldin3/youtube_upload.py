"""Upload de vídeo no YouTube via API v3 — só biblioteca padrão (urllib).

Mesmo espírito do promoter/engage.py: nada de google-api-python-client (que
puxa uma árvore de dependências enorme para a imagem slim). São 3 chamadas:

  1. refresh do access token a partir do refresh token (escopo youtube.upload
     + youtube.force-ssl);
  2. upload RESUMÁVEL do vídeo (POST inicia a sessão, um único PUT manda os
     bytes — arquivos de ~8 MB não precisam de chunk);
  3. playlistItems.insert para adicionar o vídeo à playlist (opcional).

Contrato: funções levantam RuntimeError com contexto em qualquer falha.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URI = "https://oauth2.googleapis.com/token"
UPLOAD_URI = (
    "https://www.googleapis.com/upload/youtube/v3/videos"
    "?uploadType=resumable&part=snippet,status"
)
PLAYLIST_ITEMS_URI = "https://www.googleapis.com/youtube/v3/playlistItems?part=snippet"
HTTP_TIMEOUT = float(os.environ.get("WORLDIN3_YT_HTTP_TIMEOUT_SEC", "60"))
UPLOAD_TIMEOUT = float(os.environ.get("WORLDIN3_YT_UPLOAD_TIMEOUT_SEC", "600"))

TAG = "worldin3/upload"


def _log(msg):
    print(f"[{TAG}] {msg}", flush=True)


def get_access_token(client_id, client_secret, refresh_token):
    if not (client_id and client_secret and refresh_token):
        raise RuntimeError(
            "credenciais OAuth incompletas: exige YOUTUBE_OAUTH_CLIENT_ID, "
            "_CLIENT_SECRET e YOUTUBE_OAUTH_REFRESH_TOKEN_UPLOAD."
        )
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URI, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"refresh do token OAuth falhou: HTTP {e.code} {e.read().decode()[:300]}"
        ) from e
    at = data.get("access_token")
    if not at:
        raise RuntimeError(f"resposta sem access_token: {str(data)[:200]}")
    scope = data.get("scope", "")
    if "youtube.upload" not in scope:
        _log(f"AVISO: escopo do token não inclui youtube.upload ({scope!r}); "
             "o upload provavelmente vai falhar com 403 insufficientPermissions.")
    return at


def upload_video(access_token, file_path, snippet, status):
    """Faz o upload resumável e devolve o video_id."""
    if not os.path.isfile(file_path):
        raise RuntimeError(f"arquivo de vídeo não encontrado: {file_path}")
    size = os.path.getsize(file_path)
    if size == 0:
        raise RuntimeError(f"arquivo de vídeo vazio: {file_path}")

    meta = json.dumps({"snippet": snippet, "status": status}).encode("utf-8")
    init = urllib.request.Request(
        UPLOAD_URI,
        data=meta,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/*",
        },
    )
    try:
        with urllib.request.urlopen(init, timeout=HTTP_TIMEOUT) as resp:
            location = resp.headers.get("Location")
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"não consegui iniciar a sessão de upload: HTTP {e.code} "
            f"{e.read().decode()[:400]}"
        ) from e
    if not location:
        raise RuntimeError("sessão de upload iniciada sem header Location.")

    _log(f"enviando {size/1e6:.2f} MB para a sessão resumável…")
    with open(file_path, "rb") as f:
        body = f.read()
    put = urllib.request.Request(
        location,
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "video/*",
            "Content-Length": str(size),
        },
    )
    try:
        with urllib.request.urlopen(put, timeout=UPLOAD_TIMEOUT) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"upload dos bytes falhou: HTTP {e.code} {e.read().decode()[:400]}"
        ) from e
    vid = data.get("id")
    if not vid:
        raise RuntimeError(f"resposta do upload sem id de vídeo: {str(data)[:300]}")
    return vid


def add_to_playlist(access_token, playlist_id, video_id):
    payload = json.dumps({
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
    }).encode("utf-8")
    req = urllib.request.Request(
        PLAYLIST_ITEMS_URI,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"playlistItems.insert falhou: HTTP {e.code} {e.read().decode()[:400]}"
        ) from e
    return data.get("id")


def watch_url(video_id):
    """Sempre no formato watch?v= — o validador do TubeOptimizer REJEITA
    youtube.com/live/<id> e aceita watch?v= / youtu.be."""
    return f"https://www.youtube.com/watch?v={video_id}"
