"""
Ponte de comunicação com o TubeOptimizer (app publicado no Replit).

ESTADO ATUAL
------------
Peça de conexão PRONTA E TESTÁVEL, mas ainda NÃO acoplada a nada no pipeline.
Nada chama esta função automaticamente. Quando a geração automática de Shorts
existir, o serviço que a fizer importa `publicar_no_tubeoptimizer()` e chama
com a URL do YouTube do Short recém-publicado.

CONFIG (tudo via env, sem rebuild)
----------------------------------
  TUBEOPTIMIZER_BASE_URL     URL raiz do app no Replit
                             (ex.: https://tubeoptimizer.SEU-USUARIO.repl.co).
                             Barra final é normalizada. SEM isto a função
                             lança TubeOptimizerError antes de qualquer rede.
  TUBEOPTIMIZER_API_KEY      opcional. Se setado, vai no header "X-API-Key".
  TUBEOPTIMIZER_TIMEOUT_SEC  opcional, padrão 15 (segundos, connect+read).

CONTRATO DA API
---------------
  POST {BASE_URL}/api/videos
    corpo JSON:
      {
        "youtubeUrl":  <str>   obrigatório. O validador do TubeOptimizer
                               aceita "youtube.com/watch?v=<id>" e
                               "youtu.be/<id>"; REJEITA (400 "Invalid YouTube
                               URL") o formato "youtube.com/live/<id>" — quem
                               chamar precisa normalizar para watch?v=.
        "autoPublish": <bool>  dispara publicação cross-platform DE VERDADE
                               quando true — use false em teste,
        "contentLine": <str>   linha de conteúdo p/ segmentação. Campo
                               OPCIONAL no CreateVideoBody do TubeOptimizer:
                               se o lado Replit ainda não tiver implementado,
                               a chamada funciona e o campo é só ignorado
                               (não é erro deste lado).
      }
    sucesso: 201 Created + corpo JSON com o registro do vídeo criado.

Todo o acoplamento com o TubeOptimizer vive neste módulo. Qualquer falha
(env faltando, rede, timeout, HTTP de erro, corpo não-JSON) vira
`TubeOptimizerError` com mensagem explícita — nunca uma exceção crua sem
contexto.
"""

import json
import os
import sys

import requests

TAG = "TubeOptimizer"

DEFAULT_TIMEOUT_SEC = 15.0
_VIDEOS_PATH = "/api/videos"


class TubeOptimizerError(RuntimeError):
    """Falha de comunicação ou de resposta do TubeOptimizer, já contextualizada."""


def _log(msg: str) -> None:
    print(f"{TAG} → {msg}", flush=True)


def _resolve_base_url(base_url: str | None) -> str:
    raw = (base_url if base_url is not None else os.environ.get("TUBEOPTIMIZER_BASE_URL", "")).strip()
    if not raw:
        raise TubeOptimizerError(
            "TUBEOPTIMIZER_BASE_URL não configurada (env ou argumento). "
            "Defina a URL raiz do app no Replit antes de chamar."
        )
    if not raw.startswith(("http://", "https://")):
        raise TubeOptimizerError(
            f"TUBEOPTIMIZER_BASE_URL inválida ({raw!r}): precisa começar com http:// ou https://."
        )
    return raw.rstrip("/")


def _resolve_timeout(timeout: float | None) -> float:
    if timeout is not None:
        return float(timeout)
    raw = os.environ.get("TUBEOPTIMIZER_TIMEOUT_SEC", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SEC
    try:
        return float(raw)
    except ValueError:
        _log(f"AVISO: TUBEOPTIMIZER_TIMEOUT_SEC inválido ({raw!r}); usando {DEFAULT_TIMEOUT_SEC}s.")
        return DEFAULT_TIMEOUT_SEC


def publicar_no_tubeoptimizer(
    youtube_url: str,
    auto_publish: bool = True,
    content_line: str = "futurenews",
    *,
    base_url: str | None = None,
    timeout: float | None = None,
) -> dict:
    """
    Dispara no TubeOptimizer a geração de conteúdo + publicação cross-platform
    para um vídeo do YouTube já existente.

    Parâmetros
    ----------
    youtube_url : str
        URL pública do vídeo do YouTube (obrigatório, não vazio).
    auto_publish : bool
        Passa como "autoPublish". True = publica DE VERDADE nas redes; em
        teste use False.
    content_line : str
        Passa como "contentLine". Padrão "futurenews".
    base_url : str | None
        Sobrescreve TUBEOPTIMIZER_BASE_URL (útil em teste).
    timeout : float | None
        Sobrescreve TUBEOPTIMIZER_TIMEOUT_SEC / o padrão de 15s.

    Retorno
    -------
    dict
        Corpo JSON da resposta de sucesso (o registro do vídeo criado).

    Lança
    -----
    TubeOptimizerError
        Em QUALQUER falha: env ausente, argumento inválido, rede/DNS, timeout,
        status HTTP de erro, ou corpo de resposta que não é JSON.
    """
    if not youtube_url or not str(youtube_url).strip():
        raise TubeOptimizerError("youtube_url vazio: informe a URL pública do vídeo do YouTube.")

    url_base = _resolve_base_url(base_url)
    endpoint = f"{url_base}{_VIDEOS_PATH}"
    to = _resolve_timeout(timeout)

    payload = {
        "youtubeUrl": str(youtube_url).strip(),
        "autoPublish": bool(auto_publish),
        "contentLine": content_line,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    api_key = os.environ.get("TUBEOPTIMIZER_API_KEY", "").strip()
    if api_key:
        headers["X-API-Key"] = api_key

    _log(
        f"POST {endpoint} | youtubeUrl={payload['youtubeUrl']} "
        f"autoPublish={payload['autoPublish']} contentLine={payload['contentLine']!r} timeout={to}s"
    )

    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=to)
    except requests.exceptions.ConnectTimeout as e:
        raise TubeOptimizerError(f"timeout de conexão ({to}s) ao chamar {endpoint}: {e}") from e
    except requests.exceptions.ReadTimeout as e:
        raise TubeOptimizerError(
            f"timeout de leitura ({to}s) ao chamar {endpoint} — o TubeOptimizer não respondeu a tempo: {e}"
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise TubeOptimizerError(
            f"erro de rede/DNS ao chamar {endpoint} (app no ar? URL certa?): {e}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise TubeOptimizerError(f"falha inesperada de requisição ao chamar {endpoint}: {e}") from e

    body_snippet = (resp.text or "")[:500]

    if resp.status_code not in (200, 201):
        raise TubeOptimizerError(
            f"TubeOptimizer respondeu HTTP {resp.status_code} em {endpoint}. Corpo: {body_snippet}"
        )

    try:
        data = resp.json()
    except ValueError as e:
        raise TubeOptimizerError(
            f"TubeOptimizer respondeu HTTP {resp.status_code} mas o corpo não é JSON. "
            f"Corpo: {body_snippet}"
        ) from e

    if not isinstance(data, dict):
        raise TubeOptimizerError(
            f"TubeOptimizer respondeu HTTP {resp.status_code} com JSON que não é um objeto: {data!r}"
        )

    _log(f"OK HTTP {resp.status_code} | resposta: {json.dumps(data, ensure_ascii=False)[:500]}")
    return data


# --- Execução direta: teste manual da ponte -------------------------------
# Exemplos:
#   TUBEOPTIMIZER_BASE_URL=https://... python3 lib/tubeoptimizer_client.py \
#       --url "https://www.youtube.com/watch?v=XXXX" --no-auto-publish
#   python3 lib/tubeoptimizer_client.py --url "..." --base-url https://... --content-line futurenews
def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Teste manual da ponte com o TubeOptimizer (POST /api/videos)."
    )
    parser.add_argument("--url", required=True, help="URL pública do vídeo do YouTube.")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Sobrescreve TUBEOPTIMIZER_BASE_URL.",
    )
    parser.add_argument(
        "--content-line",
        default="futurenews",
        help='contentLine a enviar (padrão: "futurenews").',
    )
    ap = parser.add_mutually_exclusive_group()
    ap.add_argument(
        "--auto-publish",
        dest="auto_publish",
        action="store_true",
        help="autoPublish=true (PUBLICA DE VERDADE nas redes).",
    )
    ap.add_argument(
        "--no-auto-publish",
        dest="auto_publish",
        action="store_false",
        help="autoPublish=false (padrão em teste).",
    )
    parser.set_defaults(auto_publish=False)
    parser.add_argument("--timeout", type=float, default=None, help="Timeout em segundos.")
    args = parser.parse_args(argv)

    try:
        data = publicar_no_tubeoptimizer(
            args.url,
            auto_publish=args.auto_publish,
            content_line=args.content_line,
            base_url=args.base_url,
            timeout=args.timeout,
        )
    except TubeOptimizerError as e:
        _log(f"ERRO: {e}")
        return 1

    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
