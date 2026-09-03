"""
Cliente de ciclo de vida do pod GPU do RunPod, orquestrado via TubeOptimizer.

PARA QUE SERVE
--------------
O MuseTalk (lip-sync self-hosted) roda num pod GPU do RunPod que é CARO por
minuto ligado. Este módulo liga o pod só quando há trabalho, espera ele ficar
de pé, manda o job de lip-sync e DESLIGA o pod logo em seguida — sempre, mesmo
se o job falhar no meio. O objetivo prático é não deixar GPU acesa cobrando à
toa.

Quem de fato liga/desliga a máquina é o app no Replit (TubeOptimizer), que fala
com a API do RunPod. Aqui a gente só chama os endpoints /gpu/* dele.

ESTADO ATUAL
------------
Módulo ISOLADO e testável sozinho. NADA no pipeline importa isto ainda —
nem o synthesizer, nem o worldin3. O acoplamento é um passo futuro e
deliberado. Rode `python3 lib/gpu_client.py --help` para o teste manual.

O passo `generate_lipsync()` é um STUB: o MuseTalk ainda não está deployado no
pod, então o endpoint real não existe. Veja o TODO gritante na função.

CONFIG (tudo via env, sem rebuild)
----------------------------------
  TUBEOPTIMIZER_BASE_URL     URL raiz do app no Replit. Mesma env já usada por
                             lib/tubeoptimizer_client.py — reaproveitada de
                             propósito para não ter duas fontes de verdade.
                             (aceita também TUBEOPTIMIZER_URL como alias.)
  TUBEOPTIMIZER_API_KEY      opcional. Se setado, vai no header "X-API-Key".
  TUBEOPTIMIZER_TIMEOUT_SEC  opcional, padrão 15. Timeout (connect+read) de
                             CADA request HTTP individual — não do ciclo todo.
  GPU_START_TIMEOUT_SEC      opcional, padrão 180. Teto do polling de boot do
                             pod em start_gpu(): estourou, vira GpuError.
  GPU_POLL_INTERVAL_SEC      opcional, padrão 5. Intervalo entre polls de
                             GET /gpu/status.
  GPU_LIPSYNC_MOCK           opcional, padrão "true". Enquanto true,
                             generate_lipsync() NÃO faz rede: devolve um
                             resultado-stub. Setar "false" antes de o MuseTalk
                             existir faz a função lançar NotImplementedError
                             de propósito.

CONTRATO DA API  (ESPERADO — confirmar com o lado Replit)
--------------------------------------------------------
  POST {BASE_URL}/gpu/start
    Liga o pod (idempotente do lado Replit — chamar com pod já ligado deve
    ser OK). Resposta: 200/201/202 + JSON qualquer (não é usado aqui).

  GET {BASE_URL}/gpu/status
    Resposta 200 + JSON:
      {
        "status": <str>   um de: "STARTING" | "RUNNING" | "STOPPING"
                          | "STOPPED" | "ERROR"  (case-insensitive aqui).
        "proxyUrl": <str> URL pública de proxy do pod (só quando RUNNING).
                          Aceitamos também as chaves: "proxy_url", "url",
                          "publicUrl", "podUrl" — a primeira não-vazia vence.
      }
    status == "ERROR"  → start_gpu() aborta na hora com GpuError.

  POST {BASE_URL}/gpu/stop
    Desliga o pod (idempotente — chamar com pod já desligado deve ser OK).
    Resposta: 200/202 + JSON qualquer.

Qualquer falha (env faltando, rede, timeout, HTTP de erro, corpo não-JSON,
boot que não completa a tempo) vira `GpuError` com mensagem explícita —
nunca uma exceção crua sem contexto.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager

import requests

TAG = "GpuClient"

DEFAULT_HTTP_TIMEOUT_SEC = 15.0
DEFAULT_START_TIMEOUT_SEC = 180.0
DEFAULT_POLL_INTERVAL_SEC = 5.0

_START_PATH = "/gpu/start"
_STATUS_PATH = "/gpu/status"
_STOP_PATH = "/gpu/stop"

# Chaves de status onde o lado Replit PODE devolver a URL de proxy do pod.
_PROXY_URL_KEYS = ("proxyUrl", "proxy_url", "url", "publicUrl", "podUrl")

_STATUS_RUNNING = "RUNNING"
_STATUS_ERROR = "ERROR"


class GpuError(RuntimeError):
    """Falha no ciclo de vida do pod GPU, já contextualizada."""


def _log(msg: str) -> None:
    print(f"{TAG} → {msg}", flush=True)


# --- Resolução de config -------------------------------------------------


def _resolve_base_url(base_url: str | None) -> str:
    raw = (
        base_url
        if base_url is not None
        else (
            os.environ.get("TUBEOPTIMIZER_BASE_URL", "")
            or os.environ.get("TUBEOPTIMIZER_URL", "")
        )
    ).strip()
    if not raw:
        raise GpuError(
            "TUBEOPTIMIZER_BASE_URL não configurada (env ou argumento). "
            "Defina a URL raiz do app no Replit antes de mexer no pod GPU."
        )
    if not raw.startswith(("http://", "https://")):
        raise GpuError(
            f"TUBEOPTIMIZER_BASE_URL inválida ({raw!r}): precisa começar com http:// ou https://."
        )
    return raw.rstrip("/")


def _resolve_float_env(name: str, default: float, override: float | None = None) -> float:
    if override is not None:
        return float(override)
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        _log(f"AVISO: {name} inválido ({raw!r}); usando {default}.")
        return default


def _resolve_http_timeout(timeout: float | None) -> float:
    return _resolve_float_env("TUBEOPTIMIZER_TIMEOUT_SEC", DEFAULT_HTTP_TIMEOUT_SEC, timeout)


def _mock_enabled() -> bool:
    raw = os.environ.get("GPU_LIPSYNC_MOCK", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    api_key = os.environ.get("TUBEOPTIMIZER_API_KEY", "").strip()
    if api_key:
        h["X-API-Key"] = api_key
    return h


# --- HTTP com erro sempre contextualizado ------------------------------


def _request(method: str, endpoint: str, timeout: float, *, expect_json: bool) -> dict | None:
    try:
        resp = requests.request(method, endpoint, headers=_headers(), timeout=timeout)
    except requests.exceptions.ConnectTimeout as e:
        raise GpuError(f"timeout de conexão ({timeout}s) em {method} {endpoint}: {e}") from e
    except requests.exceptions.ReadTimeout as e:
        raise GpuError(
            f"timeout de leitura ({timeout}s) em {method} {endpoint} — o Replit não respondeu a tempo: {e}"
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise GpuError(
            f"erro de rede/DNS em {method} {endpoint} (app no ar? URL certa?): {e}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise GpuError(f"falha inesperada de requisição em {method} {endpoint}: {e}") from e

    body_snippet = (resp.text or "")[:500]

    if resp.status_code not in (200, 201, 202):
        raise GpuError(
            f"Replit respondeu HTTP {resp.status_code} em {method} {endpoint}. Corpo: {body_snippet}"
        )

    if not expect_json:
        return None

    try:
        data = resp.json()
    except ValueError as e:
        raise GpuError(
            f"HTTP {resp.status_code} em {method} {endpoint} mas o corpo não é JSON. Corpo: {body_snippet}"
        ) from e

    if not isinstance(data, dict):
        raise GpuError(
            f"HTTP {resp.status_code} em {method} {endpoint} com JSON que não é objeto: {data!r}"
        )
    return data


def _extract_proxy_url(status_body: dict) -> str:
    for key in _PROXY_URL_KEYS:
        val = status_body.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


# --- API pública -------------------------------------------------------


def start_gpu(
    *,
    base_url: str | None = None,
    http_timeout: float | None = None,
    start_timeout: float | None = None,
    poll_interval: float | None = None,
) -> str:
    """
    Liga o pod GPU e espera ele ficar de pé.

    Faz POST /gpu/start e então faz polling em GET /gpu/status a cada
    `poll_interval` (padrão 5s), até `start_timeout` (padrão 180s), até que
    status == "RUNNING" e uma URL de proxy pública esteja presente.

    Retorno
    -------
    str
        A URL de proxy pública do pod. Passe-a para generate_lipsync().

    Lança
    -----
    GpuError
        env ausente, rede/timeout, HTTP de erro, status "ERROR" reportado
        pelo Replit, ou boot que não completou dentro de start_timeout.
    """
    url_base = _resolve_base_url(base_url)
    http_to = _resolve_http_timeout(http_timeout)
    start_to = _resolve_float_env("GPU_START_TIMEOUT_SEC", DEFAULT_START_TIMEOUT_SEC, start_timeout)
    interval = _resolve_float_env("GPU_POLL_INTERVAL_SEC", DEFAULT_POLL_INTERVAL_SEC, poll_interval)

    _log(f"POST {url_base}{_START_PATH} (timeout HTTP {http_to}s)")
    _request("POST", f"{url_base}{_START_PATH}", http_to, expect_json=False)

    _log(
        f"pod pedido; aguardando RUNNING via GET {url_base}{_STATUS_PATH} "
        f"(a cada {interval}s, teto {start_to}s)"
    )
    deadline = time.monotonic() + start_to
    attempt = 0
    last_status = "?"
    while True:
        attempt += 1
        body = _request("GET", f"{url_base}{_STATUS_PATH}", http_to, expect_json=True) or {}
        last_status = str(body.get("status", "")).strip() or "?"
        norm = last_status.upper()
        _log(f"status #{attempt}: {last_status!r}")

        if norm == _STATUS_ERROR:
            raise GpuError(
                f"Replit reportou status ERROR ao ligar o pod. Corpo: "
                f"{json.dumps(body, ensure_ascii=False)[:500]}"
            )

        if norm == _STATUS_RUNNING:
            proxy_url = _extract_proxy_url(body)
            if proxy_url:
                _log(f"pod RUNNING | proxy: {proxy_url}")
                return proxy_url
            _log("status RUNNING mas sem URL de proxy ainda; seguindo o polling")

        if time.monotonic() >= deadline:
            raise GpuError(
                f"pod não ficou RUNNING dentro de {start_to}s "
                f"(último status: {last_status!r}, {attempt} tentativas). "
                f"O pod pode ter ficado LIGADO — rode stop_gpu() para garantir."
            )
        time.sleep(interval)


def stop_gpu(
    *,
    base_url: str | None = None,
    http_timeout: float | None = None,
) -> None:
    """
    Desliga o pod GPU (POST /gpu/stop). Idempotente do lado Replit.

    Lança
    -----
    GpuError
        env ausente, rede/timeout, HTTP de erro.
    """
    url_base = _resolve_base_url(base_url)
    http_to = _resolve_http_timeout(http_timeout)
    _log(f"POST {url_base}{_STOP_PATH} (timeout HTTP {http_to}s)")
    _request("POST", f"{url_base}{_STOP_PATH}", http_to, expect_json=False)
    _log("stop enviado")


def generate_lipsync(audio_path: str, image_path: str, pod_url: str) -> dict:
    """
    Gera o vídeo de lip-sync do MuseTalk rodando no pod GPU.

    ┌──────────────────────────────────────────────────────────────────────┐
    │ TODO — ENDPOINT REAL DO MUSETALK AINDA NÃO EXISTE                     │
    │                                                                      │
    │ O MuseTalk ainda NÃO está deployado no pod. Quando estiver, definir   │
    │ aqui:                                                                 │
    │   - método + rota   (provável: POST {pod_url}/inference ou /lipsync)  │
    │   - formato do corpo (provável: multipart/form-data com os arquivos   │
    │     `audio` e `image`, OU JSON com paths/URLs acessíveis pelo pod)    │
    │   - formato da resposta (arquivo mp4 em stream? JSON com URL/base64?) │
    │   - onde gravar o mp4 de saída e o que retornar (path do arquivo)     │
    │   - timeout próprio: inferência de lip-sync é LENTA, não use os 15s   │
    │     de HTTP normal — algo como 300–600s.                              │
    │                                                                      │
    │ Enquanto GPU_LIPSYNC_MOCK != "false", esta função é um STUB e não     │
    │ faz rede nenhuma.                                                     │
    └──────────────────────────────────────────────────────────────────────┘

    Parâmetros
    ----------
    audio_path : str
        Caminho local do áudio (voz sintetizada) a casar com os lábios.
    image_path : str
        Caminho local da imagem/vídeo-base do apresentador.
    pod_url : str
        URL de proxy pública devolvida por start_gpu().

    Retorno (STUB)
    --------------
    dict
        {
          "stub": True,
          "pod_url": <str>,
          "audio_path": <str>,
          "image_path": <str>,
          "output_path": None,      # nenhum arquivo gerado no stub
          "note": <str>,
        }

    Lança
    -----
    GpuError
        se pod_url vazio, ou se algum dos arquivos de entrada não existe.
    NotImplementedError
        se GPU_LIPSYNC_MOCK == "false" (você pediu o caminho real, que
        ainda não foi implementado).
    """
    if not pod_url or not str(pod_url).strip():
        raise GpuError("pod_url vazio: chame start_gpu() e passe a URL retornada.")
    for label, path in (("audio_path", audio_path), ("image_path", image_path)):
        if not path or not os.path.isfile(path):
            raise GpuError(f"{label} não aponta para um arquivo existente: {path!r}")

    if not _mock_enabled():
        raise NotImplementedError(
            "generate_lipsync(): endpoint real do MuseTalk ainda não implementado "
            "(veja o TODO na função). Deixe GPU_LIPSYNC_MOCK=true por enquanto."
        )

    # ---- STUB -------------------------------------------------------------
    # TODO(real): trocar este bloco pela chamada HTTP ao MuseTalk no pod.
    #   ex.:
    #   with open(audio_path, "rb") as a, open(image_path, "rb") as i:
    #       resp = requests.post(
    #           f"{pod_url.rstrip('/')}/inference",
    #           files={"audio": a, "image": i},
    #           timeout=(15, 600),
    #       )
    #   resp.raise_for_status()
    #   out = os.path.join(tempfile.gettempdir(), "lipsync.mp4")
    #   with open(out, "wb") as f: f.write(resp.content)
    #   return {"stub": False, "output_path": out, ...}
    _log(
        "generate_lipsync() em MODO STUB — nenhuma chamada ao pod. "
        f"pod_url={pod_url} audio={audio_path} image={image_path}"
    )
    return {
        "stub": True,
        "pod_url": pod_url,
        "audio_path": audio_path,
        "image_path": image_path,
        "output_path": None,
        "note": "STUB: MuseTalk não deployado; ver TODO em generate_lipsync().",
    }


# --- Ciclo completo com stop garantido -------------------------------


@contextmanager
def gpu_session(
    *,
    base_url: str | None = None,
    http_timeout: float | None = None,
    start_timeout: float | None = None,
    poll_interval: float | None = None,
):
    """
    Context manager do ciclo de vida do pod.

        with gpu_session() as pod_url:
            generate_lipsync(audio, image, pod_url)

    Garante (try/finally) que stop_gpu() SEMPRE roda ao sair do bloco —
    inclusive se o corpo levantar exceção — para não deixar GPU acesa
    cobrando. Loga a duração total start→stop em segundos, para acompanhar
    custo.
    """
    t0 = time.monotonic()
    _log("=== ciclo GPU: START ===")
    pod_url = start_gpu(
        base_url=base_url,
        http_timeout=http_timeout,
        start_timeout=start_timeout,
        poll_interval=poll_interval,
    )
    t_ready = time.monotonic()
    _log(f"pod pronto em {t_ready - t0:.1f}s")
    try:
        yield pod_url
    finally:
        try:
            stop_gpu(base_url=base_url, http_timeout=http_timeout)
        except GpuError as e:
            # Não engole: o pod PODE ter ficado ligado. Loga bem alto.
            _log(f"ATENÇÃO: stop_gpu() FALHOU — confira o pod no RunPod à mão! {e}")
            raise
        finally:
            total = time.monotonic() - t0
            _log(
                f"=== ciclo GPU: STOP === duração total start→stop: {total:.1f}s "
                f"({total / 60:.2f} min) — use isso pra estimar custo"
            )


# --- Execução direta: teste manual ----------------------------------
# Exemplos:
#   # ciclo completo (start → lipsync stub → stop), stop garantido:
#   TUBEOPTIMIZER_BASE_URL=https://... \
#     python3 lib/gpu_client.py --audio /caminho/voz.mp3 --image /caminho/rosto.png
#
#   # só ligar e desligar, sem lipsync:
#   python3 lib/gpu_client.py --skip-lipsync --base-url https://...
#
#   # só desligar (pânico: "liguei e o teste morreu"):
#   python3 lib/gpu_client.py --stop-only
def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Teste manual do ciclo de vida do pod GPU (RunPod via TubeOptimizer)."
    )
    parser.add_argument("--base-url", default=None, help="Sobrescreve TUBEOPTIMIZER_BASE_URL.")
    parser.add_argument("--audio", default=None, help="Caminho do áudio para o lip-sync.")
    parser.add_argument("--image", default=None, help="Caminho da imagem-base para o lip-sync.")
    parser.add_argument(
        "--skip-lipsync",
        action="store_true",
        help="Faz só start → stop, sem chamar generate_lipsync().",
    )
    parser.add_argument(
        "--stop-only",
        action="store_true",
        help="Só chama stop_gpu() e sai (para desligar um pod esquecido ligado).",
    )
    parser.add_argument("--http-timeout", type=float, default=None, help="Timeout de cada request HTTP (s).")
    parser.add_argument("--start-timeout", type=float, default=None, help="Teto do boot do pod (s).")
    parser.add_argument("--poll-interval", type=float, default=None, help="Intervalo entre polls de status (s).")
    args = parser.parse_args(argv)

    try:
        if args.stop_only:
            stop_gpu(base_url=args.base_url, http_timeout=args.http_timeout)
            return 0

        do_lipsync = not args.skip_lipsync
        if do_lipsync and (not args.audio or not args.image):
            parser.error("--audio e --image são obrigatórios (ou passe --skip-lipsync).")

        with gpu_session(
            base_url=args.base_url,
            http_timeout=args.http_timeout,
            start_timeout=args.start_timeout,
            poll_interval=args.poll_interval,
        ) as pod_url:
            if do_lipsync:
                result = generate_lipsync(args.audio, args.image, pod_url)
                _log(f"generate_lipsync → {json.dumps(result, ensure_ascii=False)}")
            else:
                _log("--skip-lipsync: nada a fazer com o pod; indo direto pro stop")
    except (GpuError, NotImplementedError) as e:
        _log(f"ERRO: {e}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
