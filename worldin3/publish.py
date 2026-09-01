"""Metadados e publicação de uma edição do "O Mundo em 3 Minutos".

- `build_title()` / `build_description()` — texto dinâmico (edição + data +
  chamada para a live 24h com o link real).
- `publish_edition()` — upload no YouTube (privacyStatus de
  config.PRIVACY_STATUS) + adiciona à playlist (config.PLAYLIST_ID). NÃO chama
  o TubeOptimizer.
- `push_to_tubeoptimizer()` — passo SEPARADO, manual, disparado só depois da
  aprovação humana do vídeo já no ar.
"""

import datetime

from . import config, youtube_upload as ytu

_MESES = [
    "", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
    "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def _edicao_curta(now):
    # "Edição da Manhã" -> "Manhã"
    return config.period_label(now).replace("Edição da ", "").strip()


def build_title(now=None):
    now = now or datetime.datetime.now(config.tz())
    data = f"{now.day:02d}/{now.month:02d}/{now.year}"
    title = f"O Mundo em 3 Minutos — Edição da {_edicao_curta(now)} — {data}"
    return title[:100]


def build_description(headlines, now=None):
    now = now or datetime.datetime.now(config.tz())
    data_ext = f"{now.day} de {_MESES[now.month]} de {now.year}"
    linhas = [
        f"O resumo das principais notícias do Brasil e do mundo — {_edicao_curta(now)} "
        f"de {data_ext}.",
        "",
        "Nesta edição:",
    ]
    for h in headlines:
        linhas.append(f"• {h['title']}")
    linhas += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "📡 AO VIVO 24 HORAS: nossa transmissão de notícias roda sem parar, "
        "o dia inteiro. Assista a qualquer hora:",
        config.LIVE_URL,
        "",
        "Inscreva-se no canal, deixe seu like e ative o sininho para não perder "
        "as próximas edições.",
        "",
        "#OMundoEm3Minutos #notícias #jornal #shorts",
    ]
    return "\n".join(linhas)[:5000]


def build_snippet(headlines, now=None):
    return {
        "title": build_title(now),
        "description": build_description(headlines, now),
        "tags": config.YT_TAGS,
        "categoryId": config.YT_CATEGORY_ID,
        "defaultLanguage": "pt-BR",
        "defaultAudioLanguage": "pt-BR",
    }


def publish_edition(mp4_path, headlines, now=None, privacy=None):
    """Sobe o vídeo e (se houver) adiciona à playlist. Retorna dict com
    video_id, url (watch?v=), title, privacy, playlist_item_id."""
    privacy = (privacy or config.PRIVACY_STATUS or "unlisted").lower()
    if privacy not in ("private", "unlisted", "public"):
        raise RuntimeError(f"WORLDIN3_PRIVACY_STATUS inválido: {privacy!r}")

    token = ytu.get_access_token(
        config.YOUTUBE_OAUTH_CLIENT_ID,
        config.YOUTUBE_OAUTH_CLIENT_SECRET,
        config.YOUTUBE_OAUTH_REFRESH_TOKEN_UPLOAD,
    )
    snippet = build_snippet(headlines, now)
    status = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": False,
        "madeForKids": False,
    }
    print(f"[worldin3/publish] upload '{snippet['title']}' como {privacy}…", flush=True)
    video_id = ytu.upload_video(token, mp4_path, snippet, status)
    url = ytu.watch_url(video_id)
    print(f"[worldin3/publish] OK vídeo {video_id} -> {url}", flush=True)

    playlist_item_id = None
    if config.PLAYLIST_ID:
        try:
            playlist_item_id = ytu.add_to_playlist(token, config.PLAYLIST_ID, video_id)
            print(f"[worldin3/publish] adicionado à playlist {config.PLAYLIST_ID} "
                  f"(item {playlist_item_id}).", flush=True)
        except RuntimeError as e:
            print(f"[worldin3/publish] AVISO: não adicionou à playlist: {e}", flush=True)
    else:
        print("[worldin3/publish] WORLDIN3_PLAYLIST_ID vazio; pulei a playlist.", flush=True)

    return {
        "video_id": video_id,
        "url": url,
        "title": snippet["title"],
        "privacy": privacy,
        "playlist_id": config.PLAYLIST_ID or None,
        "playlist_item_id": playlist_item_id,
    }


def push_to_tubeoptimizer(youtube_url, auto_publish=True):
    """Passo manual: só depois de aprovar o vídeo no ar. Usa a ponte pronta
    lib/tubeoptimizer_client.py com contentLine=config.TUBEOPTIMIZER_CONTENT_LINE."""
    if not config.ENABLE_TUBEOPTIMIZER:
        raise RuntimeError(
            "WORLDIN3_ENABLE_TUBEOPTIMIZER=false — ligue a env var antes de "
            "disparar o TubeOptimizer."
        )
    try:
        from .lib.tubeoptimizer_client import publicar_no_tubeoptimizer
    except ImportError:
        from lib.tubeoptimizer_client import publicar_no_tubeoptimizer  # execução fora do container

    print(f"[worldin3/publish] TubeOptimizer: {youtube_url} "
          f"autoPublish={auto_publish} contentLine={config.TUBEOPTIMIZER_CONTENT_LINE!r}",
          flush=True)
    return publicar_no_tubeoptimizer(
        youtube_url,
        auto_publish=auto_publish,
        content_line=config.TUBEOPTIMIZER_CONTENT_LINE,
    )
