"""
Vídeo temático (b-roll) para a "TV virtual" do cenário do estúdio.

A moldura assets/tv_frame.png fica no lado esquerdo da composição e mostra,
DENTRO do recorte transparente da "tela", um vídeo relacionado à notícia
principal do bloco atual.

FONTES (nesta ordem — a 1ª que entregar um arquivo utilizável vence)
------------------------------------------------------------------
  1. CANAL PRÓPRIO no YouTube (FutureVerse & Beyond) — conteúdo nosso, sem
     risco de direito autoral. UMA busca por BLOCO (search.list restrito ao
     nosso canal), query = título da notícia + categoria; baixa só o 1º
     resultado, SOB DEMANDA, via yt-dlp (nunca a biblioteca toda, e só os
     primeiros TV_YT_CLIP_SECONDS segundos — a TV só roda o clipe em loop
     atrás da moldura).
  2. PEXELS — vídeo genérico de ambientação (b-roll de banco), como antes.
  3. TV_FALLBACK_CLIP — clipe estático local, se existir.
Se as três falharem -> None e o renderer compõe o bloco SEM a TV.

Todo o acoplamento com API externa vive neste módulo. O renderer só chama
`get_broll_video(categoria, titulo)` e, se receber None, compõe o bloco SEM a
TV, exatamente como antes (mesmo padrão do fallback da ElevenLabs / D-ID).

LIGA/DESLIGA
------------
ENABLE_TV (env, padrão "true")   -> desliga a TV inteira.
ENABLE_TV_YOUTUBE (env, "true")  -> desliga só a fonte 1 (vai direto pro Pexels).
YOUTUBE_API_KEY em branco        -> idem (fonte 1 nunca é tentada).

CACHE
-----
Um arquivo por CATEGORIA:
  - YouTube: assets/tv/yt/{slug}.mp4
  - Pexels : assets/tv/{slug}.mp4
Se já existe (> 0 bytes) é reusado sem tocar em API nenhuma. A ordem de leitura
do cache é YouTube -> Pexels. Para FORÇAR a rebusca de uma categoria, apague os
dois {slug}.mp4 (o do yt/ e o de cima).

COTA YouTube Data API v3
------------------------
search.list custa 100 unidades/chamada; cota padrão 10.000/dia. Como a busca é
1x por categoria (depois vira cache) o consumo real é baixo. channels.list
(resolve handle -> channelId, 1x por processo) custa 1 unidade.

API Pexels (docs oficiais, conferidas em 2026-08)
------------------------------------------------
  - Endpoint: GET https://api.pexels.com/videos/search
    (os endpoints de VÍDEO não ficam sob /v1 — só os de foto)
  - Auth: header  Authorization: <PEXELS_API_KEY>   (a chave crua, sem "Bearer")
  - Query params: query (obrigatório), orientation=landscape|portrait|square,
    size=large(4K)|medium(FullHD)|small(HD), per_page (1..80, padrão 15), page.
  - Resposta: {"videos": [{"id", "width", "height", "duration",
      "video_files": [{"id", "quality": "hd"|"sd"|"uhd"|"hls"|null,
        "file_type": "video/mp4", "width", "height", "fps", "link"}]}], ...}
  - Rate limit: headers X-Ratelimit-Limit / -Remaining / -Reset.
"""

import glob
import os
import re
import shutil
import subprocess
import tempfile
import time

import requests

TAG = "TV-BROLL"

# --- Configuração comum (tudo via env, sem rebuild) ---
ENABLE_TV = os.environ.get("ENABLE_TV", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

# Teto de resolução do arquivo baixado do Pexels: entre os mp4 de um vídeo,
# pega o de maior resolução que NÃO passe disso (evita baixar 4K à toa). Se
# todos passarem, pega o menor deles.
TV_MAX_HEIGHT = int(os.environ.get("TV_MAX_HEIGHT", "1080"))

TV_BROLL_DIR = os.environ.get("TV_BROLL_DIR", "/app/assets/tv")
os.makedirs(TV_BROLL_DIR, exist_ok=True)

# Clipe genérico salvo de antemão, usado quando as duas fontes online falham.
# Opcional: se o caminho não existir, o fallback simplesmente é "sem TV".
TV_FALLBACK_CLIP = os.environ.get(
    "TV_FALLBACK_CLIP", os.path.join(TV_BROLL_DIR, "_fallback.mp4")
).strip()

# --- Fonte 1: canal próprio no YouTube ---
ENABLE_TV_YOUTUBE = os.environ.get("ENABLE_TV_YOUTUBE", "true").strip().lower() in (
    "1", "true", "yes", "on",
)
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
# Handle do canal (com ou sem @). Resolvido para channelId via channels.list na
# 1ª busca e cacheado em memória. YOUTUBE_CHANNEL_ID, se setado, pula a resolução.
YOUTUBE_CHANNEL_HANDLE = os.environ.get(
    "YOUTUBE_CHANNEL_HANDLE", "@FutureVerse-Beyond"
).strip()
YOUTUBE_CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "").strip()
YT_SEARCH_URL = os.environ.get(
    "YT_SEARCH_URL", "https://www.googleapis.com/youtube/v3/search"
).strip()
YT_CHANNELS_URL = os.environ.get(
    "YT_CHANNELS_URL", "https://www.googleapis.com/youtube/v3/channels"
).strip()
YT_HTTP_TIMEOUT_SEC = float(os.environ.get("YT_HTTP_TIMEOUT_SEC", "20"))
YT_MAX_RESULTS = int(os.environ.get("YT_MAX_RESULTS", "5"))
# Baixa só os primeiros N s do vídeo do canal (o b-roll roda em loop atrás da
# moldura; não precisa do vídeo inteiro). 0 = vídeo completo.
TV_YT_CLIP_SECONDS = int(os.environ.get("TV_YT_CLIP_SECONDS", "90"))
TV_YT_DL_TIMEOUT_SEC = float(os.environ.get("TV_YT_DL_TIMEOUT_SEC", "180"))
# yt-dlp -f: só vídeo (a TV é muda), teto de 720p (a "tela" tem ~477 px).
TV_YT_FORMAT = os.environ.get(
    "TV_YT_FORMAT", "bv*[height<=720]/bv*[height<=1080]/b[height<=720]/b"
).strip()
TV_YT_DIR = os.environ.get("TV_YT_DIR", os.path.join(TV_BROLL_DIR, "yt")).strip()
os.makedirs(TV_YT_DIR, exist_ok=True)

# --- Pexels ---
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "").strip()
PEXELS_SEARCH_URL = os.environ.get(
    "PEXELS_SEARCH_URL", "https://api.pexels.com/videos/search"
).strip()
PEXELS_HTTP_TIMEOUT_SEC = float(os.environ.get("PEXELS_HTTP_TIMEOUT_SEC", "30"))
PEXELS_PER_PAGE = int(os.environ.get("PEXELS_PER_PAGE", "15"))

# --- Categoria da notícia -> termo de busca no Pexels ---------------------
# A categoria do classifier é um rótulo JORNALÍSTICO; jogada crua no Pexels ela
# cai no sentido de BANCO DE IMAGENS, que às vezes é outro. O caso que motivou
# isto: "Security" (notícia = ataque militar perto de Kiev) buscava "security"
# no Pexels e voltava vídeo de placa de circuito / cadeado / CFTV — o sentido
# de "segurança da informação". O mapa abaixo traduz cada categoria para uma
# consulta que se comporta bem numa busca de b-roll (pensada como "que imagem
# genérica isso traz", não como editoria).
#
# Categorias reais vêm de classifier/main.py (CATEGORIES) + "World" (fallback
# quando nenhuma palavra-chave casa). Ajuste os termos aqui, sem rebuild:
# CATEGORY_PEXELS_QUERY já lê overrides de env no formato
# TV_PEXELS_QUERY_<CATEGORIA_MAIÚSCULA> (ex.: TV_PEXELS_QUERY_SECURITY="war").
_CATEGORY_PEXELS_QUERY = {
    # rótulo jornalístico    termo de banco de imagens
    "Politics":      "government parliament flag speech",
    "Economy":       "stock market finance economy business",
    "Technology":    "technology digital data network",
    "Health":        "hospital healthcare doctor medicine",
    "Science":       "science laboratory research experiment",
    "Climate":       "climate change extreme weather nature",
    "Security":      "military conflict",
    "Entertainment": "concert stage cinema red carpet",
    "Sports":        "stadium sports competition crowd",
    "Lifestyle":     "city street people lifestyle",
    "World":         "earth globe world map international",
}
# Usado quando a categoria não está no mapa (rótulo novo no classifier, etc.).
_PEXELS_QUERY_DEFAULT = os.environ.get(
    "TV_PEXELS_QUERY_DEFAULT", "newsroom broadcast breaking news"
).strip()


def _pexels_query(category):
    """Termo de busca no Pexels para uma categoria de notícia. Ordem:
    override de env (TV_PEXELS_QUERY_<CATEGORIA>) -> mapa fixo -> default."""
    cat = (category or "").strip()
    env_override = os.environ.get(
        "TV_PEXELS_QUERY_" + re.sub(r"[^A-Za-z0-9]+", "_", cat).upper()
    )
    if env_override and env_override.strip():
        return env_override.strip()
    return _CATEGORY_PEXELS_QUERY.get(cat, _PEXELS_QUERY_DEFAULT)


# channelId resolvido do handle (cache de processo).
_CHANNEL_ID = None


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "generic"


def _usable(path):
    return bool(path) and os.path.exists(path) and os.path.getsize(path) > 0


def _fallback():
    """Clipe genérico local, ou None (renderer compõe sem a TV)."""
    if _usable(TV_FALLBACK_CLIP):
        print(f"{TAG} → usando clipe de fallback local: {TV_FALLBACK_CLIP}", flush=True)
        return TV_FALLBACK_CLIP
    print(f"{TAG} → sem fallback local; o bloco sai SEM a TV.", flush=True)
    return None


# ---------------------------------------------------------------------------
# Fonte 1: canal próprio no YouTube
# ---------------------------------------------------------------------------
def _resolve_channel_id():
    """channelId do nosso canal: usa YOUTUBE_CHANNEL_ID se setado, senão resolve
    YOUTUBE_CHANNEL_HANDLE via channels.list (1x, cacheado em memória)."""
    global _CHANNEL_ID
    if YOUTUBE_CHANNEL_ID:
        return YOUTUBE_CHANNEL_ID
    if _CHANNEL_ID:
        return _CHANNEL_ID
    handle = (YOUTUBE_CHANNEL_HANDLE or "").lstrip("@").strip()
    if not handle:
        return None
    try:
        resp = requests.get(
            YT_CHANNELS_URL,
            params={"key": YOUTUBE_API_KEY, "part": "id", "forHandle": handle},
            timeout=YT_HTTP_TIMEOUT_SEC,
        )
        if resp.status_code != 200:
            print(
                f"{TAG} → YouTube: channels.list HTTP {resp.status_code}: "
                f"{resp.text[:200]}",
                flush=True,
            )
            return None
        items = resp.json().get("items", []) or []
        if not items:
            print(f"{TAG} → YouTube: handle @{handle} não encontrado.", flush=True)
            return None
        _CHANNEL_ID = items[0]["id"]
        print(f"{TAG} → YouTube: canal @{handle} -> channelId {_CHANNEL_ID}.", flush=True)
        return _CHANNEL_ID
    except Exception as e:
        print(
            f"{TAG} → YouTube: falha ao resolver handle ({type(e).__name__}: {e}).",
            flush=True,
        )
        return None


def _build_query(category, title):
    """Query da search.list: título da notícia + categoria. Encurta p/ manter o
    foco nas palavras mais fortes (a API aceita queries longas de qualquer jeito)."""
    parts = [p.strip() for p in (title, category) if p and p.strip()]
    q = " ".join(parts)
    return q[:200].strip() or "news"


def _yt_download(video_id, out_path):
    """Baixa SOB DEMANDA só o vídeo escolhido do nosso canal, via yt-dlp, e só os
    primeiros TV_YT_CLIP_SECONDS s (0 = inteiro). Vídeo sem áudio (a TV é muda).
    Retorna True se gravou out_path; False em qualquer falha (-> cai pro Pexels)."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    tmpdir = tempfile.mkdtemp(prefix="ytbroll_")
    try:
        cmd = [
            "yt-dlp", "--no-warnings", "--no-playlist", "--no-progress",
            "--socket-timeout", "30", "--retries", "2",
            "-f", TV_YT_FORMAT,
            "--remux-video", "mp4",
            "-o", os.path.join(tmpdir, "clip.%(ext)s"),
        ]
        if TV_YT_CLIP_SECONDS > 0:
            cmd += [
                "--download-sections", f"*0-{TV_YT_CLIP_SECONDS}",
                "--force-keyframes-at-cuts",
            ]
        cmd.append(url)
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TV_YT_DL_TIMEOUT_SEC
        )
        if p.returncode != 0:
            tail = " | ".join(
                (p.stderr or p.stdout or "").strip().splitlines()[-3:]
            )
            raise RuntimeError(f"yt-dlp rc={p.returncode}: {tail}")
        cands = [
            c for c in sorted(
                glob.glob(os.path.join(tmpdir, "clip.*")),
                key=lambda f: (not f.endswith(".mp4"), -os.path.getsize(f)),
            )
            if os.path.getsize(c) > 0
        ]
        if not cands:
            raise RuntimeError("yt-dlp terminou sem arquivo")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        os.replace(cands[0], out_path)
        return True
    except Exception as e:
        print(
            f"{TAG} → YouTube: download falhou ({type(e).__name__}: {e}).",
            flush=True,
        )
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _youtube_broll(category, title, out_path):
    """Fonte 1: 1 vídeo do NOSSO canal, relevante à notícia do bloco.
    Retorna out_path se achou + baixou; None caso contrário (-> Pexels)."""
    channel_id = _resolve_channel_id()
    if not channel_id:
        print(f"{TAG} → YouTube: sem channelId; pulando p/ o Pexels.", flush=True)
        return None

    query = _build_query(category, title)
    try:
        resp = requests.get(
            YT_SEARCH_URL,
            params={
                "key": YOUTUBE_API_KEY,
                "part": "snippet",
                "channelId": channel_id,
                "q": query,
                "type": "video",
                "order": "relevance",
                "maxResults": YT_MAX_RESULTS,
            },
            timeout=YT_HTTP_TIMEOUT_SEC,
        )
    except Exception as e:
        print(
            f"{TAG} → YouTube: busca falhou ({type(e).__name__}: {e}).", flush=True
        )
        return None

    if resp.status_code == 403:
        print(
            f"{TAG} → YouTube: HTTP 403 (cota esgotada ou API v3 não habilitada?): "
            f"{resp.text[:200]}",
            flush=True,
        )
        return None
    if resp.status_code != 200:
        print(f"{TAG} → YouTube: HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
        return None

    items = [
        it for it in (resp.json().get("items", []) or [])
        if it.get("id", {}).get("videoId")
    ]
    if not items:
        print(
            f"{TAG} → YouTube: nenhum vídeo no canal p/ query={query!r}.", flush=True
        )
        return None

    top = items[0]
    vid = top["id"]["videoId"]
    vtitle = top.get("snippet", {}).get("title", "")
    print(
        f"{TAG} → YouTube: query={query!r} -> vídeo {vid} ({vtitle!r}); "
        f"baixando até {TV_YT_CLIP_SECONDS}s.",
        flush=True,
    )
    if _yt_download(vid, out_path):
        print(
            f"{TAG} → YouTube b-roll salvo: {out_path} "
            f"({os.path.getsize(out_path)} bytes) — vídeo {vid}.",
            flush=True,
        )
        return out_path
    return None


# ---------------------------------------------------------------------------
# Fonte 2: Pexels
# ---------------------------------------------------------------------------
def _pick_file(video_files):
    """Escolhe o melhor link mp4 de um vídeo do Pexels.

    - só mp4 (ignora 'hls' / playlists);
    - precisa ter width/height (o Pexels às vezes manda quality=null);
    - prefere a MAIOR resolução com altura <= TV_MAX_HEIGHT; se todas
      passarem do teto, a MENOR delas.
    """
    cands = []
    for f in video_files:
        link = f.get("link") or ""
        file_type = f.get("file_type") or ""
        quality = f.get("quality")
        if "mp4" not in file_type and not link.endswith(".mp4"):
            continue
        if quality == "hls":
            continue
        h = f.get("height") or 0
        w = f.get("width") or 0
        if not h or not w:
            continue
        cands.append((int(h), int(w), link))

    if not cands:
        return None

    within = [c for c in cands if c[0] <= TV_MAX_HEIGHT]
    if within:
        within.sort(key=lambda c: (-c[0], -c[1]))
        return within[0][2]
    cands.sort(key=lambda c: (c[0], c[1]))
    return cands[0][2]


def _download(url, out_path):
    with requests.get(url, stream=True, timeout=PEXELS_HTTP_TIMEOUT_SEC) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"download status={resp.status_code}")
        tmp = out_path + ".part"
        total = 0
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if chunk:
                    fh.write(chunk)
                    total += len(chunk)
    if total == 0:
        os.remove(tmp)
        raise RuntimeError("vídeo baixado vazio")
    os.replace(tmp, out_path)
    return out_path


def _pexels_broll(category, out_path):
    """Fonte 2: vídeo de ambientação do Pexels. Retorna out_path ou None
    (nunca levanta exceção)."""
    if not PEXELS_API_KEY:
        print(f"{TAG} → Pexels: PEXELS_API_KEY não configurada.", flush=True)
        return None

    started = time.monotonic()
    query = _pexels_query(category)
    try:
        params = {
            "query": query,
            "orientation": "landscape",
            "size": "medium",
            "per_page": PEXELS_PER_PAGE,
        }
        resp = requests.get(
            PEXELS_SEARCH_URL,
            headers={"Authorization": PEXELS_API_KEY},
            params=params,
            timeout=PEXELS_HTTP_TIMEOUT_SEC,
        )
        remaining = resp.headers.get("X-Ratelimit-Remaining")
        if resp.status_code == 429:
            raise RuntimeError("rate limit atingido (HTTP 429)")
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        videos = resp.json().get("videos", []) or []
        link = None
        for v in videos:
            link = _pick_file(v.get("video_files", []) or [])
            if link:
                break
        if not link:
            print(
                f"{TAG} → Pexels: nenhum vídeo utilizável p/ categoria={category!r} "
                f"(query={query!r}, {len(videos)} resultados).",
                flush=True,
            )
            return None

        print(
            f"{TAG} → Pexels: categoria={category!r} -> query={query!r}: baixando {link} "
            f"(rate limit restante: {remaining})",
            flush=True,
        )
        _download(link, out_path)
        print(
            f"{TAG} → Pexels b-roll salvo: {out_path} "
            f"({os.path.getsize(out_path)} bytes, {time.monotonic() - started:.1f}s)",
            flush=True,
        )
        return out_path
    except Exception as e:
        # Não deixa .part nem mp4 vazio envenenar o cache da categoria.
        for p in (out_path + ".part", out_path):
            try:
                if os.path.exists(p) and (p.endswith(".part") or os.path.getsize(p) == 0):
                    os.remove(p)
            except OSError:
                pass
        print(
            f"{TAG} → Pexels: FALHA ({type(e).__name__}: {e}) p/ categoria={category!r}.",
            flush=True,
        )
        return None


# ---------------------------------------------------------------------------
# Orquestrador
# ---------------------------------------------------------------------------
def get_broll_video(category, title=None):
    """
    Caminho de um mp4 temático (b-roll) para o bloco, ou None para o renderer
    compor o bloco SEM a TV.

    Ordem: cache YouTube -> cache Pexels -> YouTube ao vivo (nosso canal) ->
    Pexels ao vivo -> clipe de fallback local -> None. Nunca levanta exceção.
    """
    if not ENABLE_TV:
        return None

    slug = _slug(category)
    yt_path = os.path.join(TV_YT_DIR, f"{slug}.mp4")
    px_path = os.path.join(TV_BROLL_DIR, f"{slug}.mp4")

    # 1) Cache (YouTube tem prioridade sobre o Pexels).
    if _usable(yt_path):
        print(f"{TAG} → cache HIT (YouTube) categoria={category!r} -> {yt_path}", flush=True)
        return yt_path
    if _usable(px_path):
        print(f"{TAG} → cache HIT (Pexels) categoria={category!r} -> {px_path}", flush=True)
        return px_path

    # 2) Fonte 1: nosso canal no YouTube.
    if ENABLE_TV_YOUTUBE and YOUTUBE_API_KEY:
        try:
            path = _youtube_broll(category, title, yt_path)
            if path:
                return path
        except Exception as e:
            print(
                f"{TAG} → YouTube: erro inesperado ({type(e).__name__}: {e}); "
                "indo pro Pexels.",
                flush=True,
            )
    elif ENABLE_TV_YOUTUBE and not YOUTUBE_API_KEY:
        print(f"{TAG} → YouTube: YOUTUBE_API_KEY não configurada; indo pro Pexels.", flush=True)

    # 3) Fonte 2: Pexels.
    try:
        path = _pexels_broll(category, px_path)
        if path:
            return path
    except Exception as e:
        print(f"{TAG} → Pexels: erro inesperado ({type(e).__name__}: {e}).", flush=True)

    # 4) Fonte 3: clipe estático local (ou None).
    return _fallback()
