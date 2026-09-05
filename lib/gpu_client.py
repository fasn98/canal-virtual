"""
Cliente de ciclo de vida do pod GPU do RunPod + chamadas ao servidor de inferência.

PARA QUE SERVE
--------------
O MuseTalk (lip-sync self-hosted) roda num pod GPU do RunPod que é CARO por
minuto ligado. Este módulo liga o pod só quando há trabalho, espera ele ficar
de pé, manda o job de lip-sync e DESLIGA o pod logo em seguida — sempre, mesmo
se o job falhar no meio. O objetivo prático é não deixar GPU acesa cobrando à
toa.

Quem liga/desliga a máquina é ESTE módulo, DIRETO na API GraphQL do RunPod
(podResume/podStop/pod) — decisão de 2026-09-05: antes ia pelo app no Replit
(TubeOptimizer), mas quem sabe QUANDO precisa gerar um vídeo é o pipeline
aqui, não o Replit; e na prática o app publicado nem tinha as rotas
/api/gpu/* no ar quando testamos (404 consistente, "Cannot GET"). O Replit
fica só de consulta opcional agora, se quiser um dashboard.

As chamadas de INFERÊNCIA de verdade (/tts, /lipsync, /generate) vão direto ao
servidor que roda DENTRO do pod, via GPU_SERVER_URL (ou a URL de proxy que
start_gpu() descobre).

ESTADO ATUAL
------------
Módulo ISOLADO e testável sozinho. NADA no pipeline importa isto ainda —
nem o synthesizer, nem o worldin3. O acoplamento é um passo futuro e
deliberado. Rode `python3 lib/gpu_client.py --help` para o teste manual.

generate_lipsync() e generate_video() têm AGORA dois caminhos, escolhidos por env:
  - GPU_LIPSYNC_MOCK=true  (padrão) → STUB, não faz rede nenhuma;
  - GPU_LIPSYNC_REAL=true          → chama o servidor de inferência de verdade.
O modo REAL tem precedência sobre o MOCK quando os dois estão ligados. Assim
dá para alternar entre stub e chamada real sem apagar o código de teste.

generate_video(texto) é o endpoint COMBINADO (TTS + lip-sync num POST só) — é
o que o pipeline vai chamar. O contrato de /generate está CONFIRMADO (abaixo):
manda {"text", "seed"}, recebe o MP4 final nos bytes do corpo.

CONFIG (tudo via env, sem rebuild)
----------------------------------
  RUNPOD_API_KEY             obrigatória pra start_gpu()/stop_gpu(). Settings
                             → API Keys no painel do RunPod. Vai no header
                             "Authorization: Bearer <key>" da API GraphQL.
  RUNPOD_POD_ID              obrigatório pra start_gpu()/stop_gpu(). É o
                             prefixo da URL de proxy (GPU_SERVER_URL) — ex.:
                             em https://ykvbdr0tnvzjqk-8000.proxy.runpod.net
                             o pod id é "ykvbdr0tnvzjqk". MUDA se o pod for
                             DESTRUÍDO e recriado (não muda em stop/resume
                             normal) — atualizar junto com GPU_SERVER_URL.
  GPU_POD_PORT               opcional, padrão 8000. Porta usada pra montar a
                             URL de proxy depois que o pod fica RUNNING
                             (https://{pod_id}-{porta}.proxy.runpod.net).
  TUBEOPTIMIZER_TIMEOUT_SEC  opcional, padrão 15. Timeout (connect+read) de
                             CADA request HTTP à API do RunPod — não do
                             ciclo todo (nome da env mantido por legado).
  GPU_START_TIMEOUT_SEC      opcional, padrão 180. Teto do polling de boot do
                             pod em start_gpu(): estourou, vira GpuError.
  GPU_POLL_INTERVAL_SEC      opcional, padrão 5. Intervalo entre polls de
                             status do pod (query GraphQL `pod`).
  GPU_READY_TIMEOUT_SEC      opcional, padrão 120. Teto do polling de
                             {pod_url}/ready depois do pod RUNNING — RunPod
                             RUNNING só confirma o container de pé, e o
                             /health do gateway responde 200 mesmo com os
                             workers (Chatterbox/MuseTalk) mortos. /ready só
                             dá 200 quando os dois workers estão de pé.

  GPU_SERVER_URL             URL raiz do servidor de inferência dentro do pod
                             (ex.: https://<pod>-8000.proxy.runpod.net). Usada
                             por generate_lipsync() no modo real, e por tts() /
                             generate_video(). Se generate_lipsync() receber um
                             pod_url não-vazio (o que start_gpu() devolve), ESSE
                             ganha; GPU_SERVER_URL é o fallback / a fonte fixa.
  GPU_LIPSYNC_MOCK           opcional, padrão "true". Enquanto true,
                             generate_lipsync() NÃO faz rede: devolve um
                             resultado-stub. "false" + sem GPU_LIPSYNC_REAL faz
                             a função lançar NotImplementedError de propósito.
  GPU_LIPSYNC_REAL           opcional, padrão "false". "true" liga o caminho
                             REAL de generate_video()/generate_lipsync()/tts():
                             POST ao servidor de inferência. Tem precedência
                             sobre GPU_LIPSYNC_MOCK.
  GPU_INFERENCE_TIMEOUT_SEC  opcional, padrão 600. Timeout (connect+read) de
                             /tts e /lipsync — que são LENTAS; não use os 15s
                             do HTTP normal.
  GENERATE_TIMEOUT_SEC       opcional, padrão 1800, PISO 1800 (valor abaixo
                             disso é elevado com aviso — o gateway dentro do
                             pod já usa 1800s internamente). Timeout
                             (connect+read) do /generate, que roda TTS +
                             lip-sync em sequência e é o mais lento.
  INFERENCE_SERVER_API_KEY   opcional (alias legado: GPU_SERVER_API_KEY). Se
                             setado, vai no header "X-API-Key: <key>" das
                             chamadas de inferência (/tts, /lipsync, /generate).

CONTRATO DA API DO RUNPOD  (confirmado em 2026-09-05, testes read-only reais)
------------------------------------------------------------------------------
  Endpoint: POST https://api.runpod.io/graphql
  Auth:     header "Authorization: Bearer <RUNPOD_API_KEY>"
  Introspecção desligada em produção — schema abaixo confirmado testando os
  campos de verdade (erro de validação de tipo == campo existe).

  mutation { podResume(input: {podId: "<id>"}) { id desiredStatus } }
    Liga o pod. Idempotente (chamar com o pod já RUNNING não quebra).

  query { pod(input: {podId: "<id>"}) { id desiredStatus runtime { uptimeInSeconds } } }
    desiredStatus: "RUNNING" | "EXITED" | ...
    runtime: null enquanto o pod não terminou de subir (mesmo já RUNNING);
             não-null quando o container está de pé de verdade. start_gpu()
             só considera pronto com desiredStatus=="RUNNING" E runtime != null.
    Pod não encontrado (RUNPOD_POD_ID errado) → data.pod vem null.

  mutation { podStop(input: {podId: "<id>"}) { id desiredStatus } }
    Desliga o pod. Idempotente.

  Erro de rede/HTTP/GraphQL ("errors" no corpo) sempre vira GpuError.

CONTRATO DO SERVIDOR DE INFERÊNCIA
---------------------------------
  POST {GPU_SERVER_URL}/generate   (CONFIRMADO)
    Header:  X-API-Key: <INFERENCE_SERVER_API_KEY>
    Corpo:   JSON {"text": <str>, "seed": <int|null>}
    200:     o BINÁRIO do MP4 final direto no corpo (TTS + lip-sync com o vídeo
             idle fixo do pod). Gravado como está — SEM parse de JSON.
    Erros:   401 chave inválida/ausente · 400/422 validação (ex.: texto vazio) ·
             5xx erro do worker de inferência. Em todos, o corpo do erro é
             logado antes de virar GpuError.

  POST {GPU_SERVER_URL}/lipsync    [PRESUMIDO — confirmar antes de usar]
    multipart/form-data com os campos de arquivo `audio` e `image`.
  POST {GPU_SERVER_URL}/tts        [PRESUMIDO — confirmar antes de usar]
    JSON {"text": <str>}.
  Resposta de /tts e /lipsync: binário direto (video/*, audio/* ou
  application/octet-stream) gravado como está, ou JSON com chave de
  URL/caminho de resultado (video_url / output_url / result_url / url /
  output / video / audio_url / path — relativa vira absoluta contra
  GPU_SERVER_URL) que baixamos, ou JSON com <algo>_base64. JSON com
  "error"/"detail" vira GpuError.

Qualquer falha (env faltando, rede, timeout, HTTP de erro, corpo inesperado,
boot que não completa a tempo) vira `GpuError` com mensagem explícita —
nunca uma exceção crua sem contexto.
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager

import requests

TAG = "GpuClient"

# --- Log persistente do ciclo do pod / inferência -------------------------
# O stdout deste módulo vai para o stdout de quem chamou (renderer, teste
# manual, ...) e some quando o container é recriado. Como o pod GPU é efêmero
# (gpu_session() o desliga ao fim de cada geração) e NÃO tem log próprio
# coletado em lugar nenhum, um incidente de geração fica indiagnosticável.
# Este handler espelha TUDO que passa por _log() para um arquivo em disco
# (volume montado). Best-effort: nunca levanta, nunca bloqueia a inferência.
#   GPU_CLIENT_LOG_FILE   caminho do arquivo. Default: /app/output/gpu_client.log
#                         se /app/output existir (renderer), senão desligado.
#   GPU_CLIENT_LOG_MAX_BYTES  rotação simples (renomeia p/ .1) acima disso.
#                             Default 5_000_000. 0 desliga a rotação.
def _resolve_log_path() -> str | None:
    # Var presente (mesmo vazia) => decisão explícita: vazia desliga.
    if "GPU_CLIENT_LOG_FILE" in os.environ:
        return os.environ["GPU_CLIENT_LOG_FILE"].strip() or None
    default_dir = "/app/output"
    if os.path.isdir(default_dir):
        return os.path.join(default_dir, "gpu_client.log")
    return None


_LOG_PATH = _resolve_log_path()


def _log_to_file(line: str) -> None:
    if not _LOG_PATH:
        return
    try:
        try:
            max_bytes = int(os.environ.get("GPU_CLIENT_LOG_MAX_BYTES", "5000000"))
        except ValueError:
            max_bytes = 5_000_000
        if max_bytes > 0 and os.path.exists(_LOG_PATH) and os.path.getsize(_LOG_PATH) > max_bytes:
            try:
                os.replace(_LOG_PATH, _LOG_PATH + ".1")
            except OSError:
                pass
        with open(_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass

DEFAULT_HTTP_TIMEOUT_SEC = 15.0
DEFAULT_START_TIMEOUT_SEC = 180.0
DEFAULT_POLL_INTERVAL_SEC = 5.0
DEFAULT_INFERENCE_TIMEOUT_SEC = 600.0
DEFAULT_GENERATE_TIMEOUT_SEC = 1800.0
# Piso do timeout de /generate: o gateway DENTRO do pod já espera até 1800s pelo
# worker, então um valor menor aqui só causaria ReadTimeout no cliente antes de
# o servidor terminar. Valores abaixo disso são elevados (com aviso no log).
MIN_GENERATE_TIMEOUT_SEC = 1800.0
# RunPod "RUNNING" só confirma o container de pé — o servidor de inferência
# (TTS/lip-sync) pode levar mais alguns segundos pra carregar os modelos.
DEFAULT_READY_TIMEOUT_SEC = 120.0

# Endpoints do servidor de inferência dentro do pod (contra GPU_SERVER_URL).
_TTS_PATH = "/tts"
_LIPSYNC_PATH = "/lipsync"
_GENERATE_PATH = "/generate"

# API GraphQL do RunPod (controle DIRETO do pod, 2026-09-05 — antes ia pelo
# app no Replit, que ficou instável/desatualizado; ver módulo docstring).
RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql"
DEFAULT_GPU_POD_PORT = 8000

# Chaves onde o servidor de inferência PODE devolver a URL/caminho do resultado.
_RESULT_URL_KEYS = (
    "video_url",
    "output_url",
    "result_url",
    "url",
    "output",
    "video",
    "audio_url",
    "path",
)
_RESULT_B64_KEYS = ("video_base64", "audio_base64", "data_base64", "base64")


class GpuError(RuntimeError):
    """Falha no ciclo de vida do pod GPU / na inferência, já contextualizada."""


def _log(msg: str) -> None:
    line = f"{TAG} → {msg}"
    print(line, flush=True)
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
    _log_to_file(f"{ts} {line}")


# --- Resolução de config -------------------------------------------------


def _resolve_gpu_server_url(explicit: str | None) -> str:
    """
    URL do servidor de inferência. Precedência: argumento explícito não-vazio
    (o pod_url que start_gpu() devolve) > env GPU_SERVER_URL.
    """
    raw = (
        explicit
        if explicit is not None and str(explicit).strip()
        else os.environ.get("GPU_SERVER_URL", "")
    )
    raw = str(raw).strip()
    if not raw:
        raise GpuError(
            "GPU_SERVER_URL não configurada (env) e nenhuma pod_url passada. "
            "Defina a URL do servidor de inferência "
            "(ex.: https://<pod>-8000.proxy.runpod.net)."
        )
    if not raw.startswith(("http://", "https://")):
        raise GpuError(
            f"URL do servidor de inferência inválida ({raw!r}): precisa começar com http:// ou https://."
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


def _resolve_inference_timeout(timeout: float | None) -> float:
    return _resolve_float_env(
        "GPU_INFERENCE_TIMEOUT_SEC", DEFAULT_INFERENCE_TIMEOUT_SEC, timeout
    )


def _resolve_generate_timeout(timeout: float | None = None) -> float:
    """Timeout do /generate: default 1800s, com PISO de 1800s (eleva com aviso)."""
    val = _resolve_float_env(
        "GENERATE_TIMEOUT_SEC", DEFAULT_GENERATE_TIMEOUT_SEC, timeout
    )
    if val < MIN_GENERATE_TIMEOUT_SEC:
        _log(
            f"AVISO: GENERATE_TIMEOUT_SEC={val:g}s abaixo do piso "
            f"({MIN_GENERATE_TIMEOUT_SEC:g}s — o gateway no pod usa esse valor); "
            f"elevando para {MIN_GENERATE_TIMEOUT_SEC:g}s."
        )
        return MIN_GENERATE_TIMEOUT_SEC
    return val


def _mock_enabled() -> bool:
    raw = os.environ.get("GPU_LIPSYNC_MOCK", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _real_enabled() -> bool:
    raw = os.environ.get("GPU_LIPSYNC_REAL", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _runpod_api_key() -> str:
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not key:
        raise GpuError(
            "RUNPOD_API_KEY não configurada — necessária pra controlar o pod "
            "direto na API do RunPod (Settings → API Keys no painel do RunPod)."
        )
    return key


def _runpod_pod_id(explicit: str | None = None) -> str:
    pid = (explicit if explicit is not None else os.environ.get("RUNPOD_POD_ID", "")).strip()
    if not pid:
        raise GpuError(
            "RUNPOD_POD_ID não configurado — necessário pra saber qual pod "
            "ligar/desligar. É o prefixo da URL de proxy (GPU_SERVER_URL)."
        )
    return pid


def _inference_headers() -> dict[str, str]:
    h = {"Accept": "application/json, */*"}
    key = (
        os.environ.get("INFERENCE_SERVER_API_KEY", "").strip()
        or os.environ.get("GPU_SERVER_API_KEY", "").strip()  # alias legado
    )
    if key:
        h["X-API-Key"] = key
    return h


# --- API GraphQL do RunPod (controle direto do pod) ---------------------


def _runpod_graphql(query: str, *, timeout: float) -> dict:
    """
    POST à API GraphQL do RunPod (RUNPOD_GRAPHQL_URL), autenticado com
    `Authorization: Bearer {RUNPOD_API_KEY}`. Levanta GpuError em erro de
    rede, HTTP != 200 ou corpo com "errors" (GraphQL valida antes de
    executar — um erro de validação NÃO chega a rodar a mutation).
    """
    try:
        resp = requests.post(
            RUNPOD_GRAPHQL_URL,
            headers={
                "Authorization": f"Bearer {_runpod_api_key()}",
                "Content-Type": "application/json",
            },
            json={"query": query},
            timeout=timeout,
        )
    except requests.exceptions.ConnectTimeout as e:
        raise GpuError(f"timeout de conexão ({timeout}s) na API do RunPod: {e}") from e
    except requests.exceptions.ReadTimeout as e:
        raise GpuError(f"timeout de leitura ({timeout}s) na API do RunPod: {e}") from e
    except requests.exceptions.ConnectionError as e:
        raise GpuError(f"erro de rede/DNS na API do RunPod: {e}") from e
    except requests.exceptions.RequestException as e:
        raise GpuError(f"falha inesperada de requisição na API do RunPod: {e}") from e

    if resp.status_code != 200:
        raise GpuError(f"API do RunPod respondeu HTTP {resp.status_code}: {(resp.text or '')[:500]}")

    try:
        body = resp.json()
    except ValueError as e:
        raise GpuError(f"API do RunPod respondeu corpo não-JSON: {(resp.text or '')[:500]}") from e

    if body.get("errors"):
        raise GpuError(f"API do RunPod retornou erro: {body['errors']}")
    return body.get("data") or {}


def _runpod_pod_status(pod_id: str, *, timeout: float) -> dict:
    """{"id", "desiredStatus", "runtime"} — runtime é None enquanto o pod não
    terminou de subir (mesmo depois do podResume ser aceito)."""
    query = (
        "query { pod(input: {podId: %s}) "
        "{ id desiredStatus runtime { uptimeInSeconds } } }" % json.dumps(pod_id)
    )
    data = _runpod_graphql(query, timeout=timeout)
    pod = data.get("pod")
    if not pod:
        raise GpuError(f"RunPod não encontrou o pod {pod_id!r} (verifique RUNPOD_POD_ID).")
    return pod


def _runpod_proxy_url(pod_id: str, port: int | None = None) -> str:
    p = port or int(os.environ.get("GPU_POD_PORT", str(DEFAULT_GPU_POD_PORT)))
    return f"https://{pod_id}-{p}.proxy.runpod.net"


# --- HTTP de inferência (respostas grandes, timeout longo) ------------


def _download_to(url: str, out_path: str, timeout: float) -> str:
    try:
        resp = requests.get(url, timeout=timeout, stream=True, headers=_inference_headers())
    except requests.exceptions.RequestException as e:
        raise GpuError(f"falha ao baixar o resultado de {url}: {e}") from e
    if resp.status_code != 200:
        raise GpuError(
            f"download do resultado falhou HTTP {resp.status_code} em {url}. "
            f"Corpo: {(resp.text or '')[:300]}"
        )
    tmp = out_path + ".part"
    total = 0
    with open(tmp, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            if chunk:
                fh.write(chunk)
                total += len(chunk)
    if total == 0:
        os.remove(tmp)
        raise GpuError(f"resultado vazio baixado de {url}")
    os.replace(tmp, out_path)
    return out_path


def _post_inference(
    server_url: str,
    path: str,
    *,
    files: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    json_body: dict | None = None,
    out_path: str,
    timeout: float,
) -> str:
    """
    POST a um endpoint do servidor de inferência e materializa o resultado em
    `out_path`. Aceita resposta binária (grava direto) ou JSON com URL/caminho
    de resultado (baixa) ou <algo>_base64 (decodifica). Devolve `out_path`.
    """
    endpoint = f"{server_url}{path}"
    opened: list = []
    file_args = None
    try:
        if files:
            file_args = {}
            for field, fpath in files.items():
                fh = open(fpath, "rb")
                opened.append(fh)
                file_args[field] = (os.path.basename(fpath), fh)
        try:
            resp = requests.post(
                endpoint,
                files=file_args,
                data=form,
                json=json_body if file_args is None and form is None else None,
                headers=_inference_headers(),
                timeout=timeout,
            )
        except requests.exceptions.ConnectTimeout as e:
            raise GpuError(
                f"timeout de conexão ({timeout}s) em POST {endpoint}: {e}"
            ) from e
        except requests.exceptions.ReadTimeout as e:
            raise GpuError(
                f"timeout de leitura ({timeout}s) em POST {endpoint} — inferência demorou "
                f"demais; suba GPU_INFERENCE_TIMEOUT_SEC: {e}"
            ) from e
        except requests.exceptions.ConnectionError as e:
            raise GpuError(
                f"erro de rede/DNS em POST {endpoint} (pod de pé? GPU_SERVER_URL certa?): {e}"
            ) from e
        except requests.exceptions.RequestException as e:
            raise GpuError(f"falha inesperada em POST {endpoint}: {e}") from e
    finally:
        for fh in opened:
            try:
                fh.close()
            except OSError:
                pass

    ctype = (resp.headers.get("Content-Type") or "").lower()
    is_json = "json" in ctype

    if resp.status_code not in (200, 201, 202):
        snippet = (resp.text or "")[:500]
        raise GpuError(
            f"servidor de inferência respondeu HTTP {resp.status_code} em POST {endpoint}. "
            f"Corpo: {snippet}"
        )

    if is_json:
        try:
            body = resp.json()
        except ValueError as e:
            raise GpuError(
                f"HTTP {resp.status_code} em POST {endpoint} com Content-Type JSON mas corpo "
                f"inválido. Corpo: {(resp.text or '')[:500]}"
            ) from e
        if isinstance(body, dict):
            err = body.get("error") or body.get("detail")
            if err:
                raise GpuError(
                    f"servidor de inferência retornou erro em POST {endpoint}: {err}"
                )
            for key in _RESULT_URL_KEYS:
                val = body.get(key)
                if isinstance(val, str) and val.strip():
                    result_url = val.strip()
                    if result_url.startswith("/"):
                        result_url = f"{server_url}{result_url}"
                    _log(f"resultado por URL ({key}): {result_url}")
                    return _download_to(result_url, out_path, timeout)
            for key in _RESULT_B64_KEYS:
                val = body.get(key)
                if isinstance(val, str) and val.strip():
                    try:
                        raw = base64.b64decode(val, validate=True)
                    except ValueError as e:  # binascii.Error é subclasse de ValueError
                        raise GpuError(
                            f"campo {key} em POST {endpoint} não é base64 válido: {e}"
                        ) from e
                    with open(out_path, "wb") as fh:
                        fh.write(raw)
                    return out_path
        raise GpuError(
            f"HTTP {resp.status_code} em POST {endpoint}: JSON sem campo de resultado "
            f"reconhecido ({', '.join(_RESULT_URL_KEYS)}...). Corpo: {(resp.text or '')[:500]}"
        )

    # Resposta binária: grava como está.
    if not resp.content:
        raise GpuError(f"HTTP {resp.status_code} em POST {endpoint} com corpo binário vazio")
    tmp = out_path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(resp.content)
    os.replace(tmp, out_path)
    return out_path


def _post_generate(
    server_url: str,
    json_body: dict,
    *,
    out_path: str,
    timeout: float,
) -> str:
    """
    POST {server_url}/generate com corpo JSON e header X-API-Key. A resposta 200
    é o BINÁRIO do MP4 direto no corpo — gravado como está, SEM parse de JSON.
    HTTP de erro vira GpuError com o corpo logado, discriminando 401 (chave),
    400/422 (validação) e 5xx (worker). Devolve `out_path`.
    """
    endpoint = f"{server_url}{_GENERATE_PATH}"
    try:
        resp = requests.post(
            endpoint,
            json=json_body,
            headers=_inference_headers(),
            timeout=timeout,
        )
    except requests.exceptions.ConnectTimeout as e:
        raise GpuError(f"timeout de conexão ({timeout:g}s) em POST {endpoint}: {e}") from e
    except requests.exceptions.ReadTimeout as e:
        raise GpuError(
            f"timeout de leitura ({timeout:g}s) em POST {endpoint} — a geração demorou "
            f"mais que o timeout; suba GENERATE_TIMEOUT_SEC: {e}"
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise GpuError(
            f"erro de rede/DNS em POST {endpoint} (pod de pé? GPU_SERVER_URL certa?): {e}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise GpuError(f"falha inesperada em POST {endpoint}: {e}") from e

    status = resp.status_code

    if status not in (200, 201, 202):
        # Só aqui vale decodificar o corpo como texto (num 200 ele é MP4 binário).
        err_body = (resp.text or "")[:1000]
        if status == 401:
            raise GpuError(
                f"POST {endpoint} → HTTP 401: chave de API inválida ou ausente. "
                f"Confira INFERENCE_SERVER_API_KEY (header X-API-Key). Corpo: {err_body}"
            )
        if status in (400, 422):
            raise GpuError(
                f"POST {endpoint} → HTTP {status}: requisição rejeitada na validação "
                f"(texto vazio? corpo malformado?). Corpo: {err_body}"
            )
        if 500 <= status <= 599:
            raise GpuError(
                f"POST {endpoint} → HTTP {status}: erro no worker de inferência do pod. "
                f"Corpo: {err_body}"
            )
        raise GpuError(f"POST {endpoint} → HTTP {status} inesperado. Corpo: {err_body}")

    data = resp.content
    if not data:
        raise GpuError(f"POST {endpoint} → HTTP {status} com corpo vazio (esperava MP4).")

    # Contrato diz binário. Se veio JSON/texto num 200, é erro mascarado — não
    # engole: mostra o corpo.
    if data[:1] in (b"{", b"["):
        raise GpuError(
            f"POST {endpoint} → HTTP {status} mas o corpo parece JSON, não um MP4. "
            f"Corpo: {data[:1000].decode('utf-8', 'replace')}"
        )
    has_ftyp = b"ftyp" in data[:64]
    _log(
        f"/generate → HTTP {status}, {len(data)} bytes, "
        f"Content-Type {resp.headers.get('Content-Type')!r}, ftyp={'sim' if has_ftyp else 'NÃO'}"
    )
    if not has_ftyp:
        _log(
            f"AVISO: resposta de {endpoint} sem caixa 'ftyp' nos 1ºs 64 bytes; "
            f"gravando assim mesmo (validação de mídia fica com o renderer)."
        )

    tmp = out_path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, out_path)
    return out_path


# --- API pública: ciclo de vida do pod -------------------------------


def start_gpu(
    *,
    pod_id: str | None = None,
    http_timeout: float | None = None,
    start_timeout: float | None = None,
    poll_interval: float | None = None,
) -> str:
    """
    Liga o pod GPU DIRETO na API do RunPod (mutation podResume) e espera ele
    ficar de pé — sem depender do app no Replit pra controle (2026-09-05: o
    Replit ficou só de consulta opcional, o app publicado nem tinha as rotas
    /api/gpu/* no ar quando testamos).

    Faz podResume(podId) e então faz polling na query `pod` a cada
    `poll_interval` (padrão 5s), até `start_timeout` (padrão 180s), até que
    desiredStatus == "RUNNING" e o runtime já tenha subido.

    Retorno
    -------
    str
        A URL de proxy do pod (https://{podId}-{GPU_POD_PORT}.proxy.runpod.net,
        porta padrão 8000). Passe-a para generate_video()/generate_lipsync().

    Lança
    -----
    GpuError
        RUNPOD_API_KEY/RUNPOD_POD_ID ausentes, rede/timeout, erro da API do
        RunPod (inclui pod inexistente), ou boot que não completou dentro de
        start_timeout.
    """
    pid = _runpod_pod_id(pod_id)
    http_to = _resolve_http_timeout(http_timeout)
    start_to = _resolve_float_env("GPU_START_TIMEOUT_SEC", DEFAULT_START_TIMEOUT_SEC, start_timeout)
    interval = _resolve_float_env("GPU_POLL_INTERVAL_SEC", DEFAULT_POLL_INTERVAL_SEC, poll_interval)

    _log(f"RunPod → podResume({pid})")
    _runpod_graphql(
        "mutation { podResume(input: {podId: %s}) { id desiredStatus } }" % json.dumps(pid),
        timeout=http_to,
    )

    _log(f"pod pedido; aguardando RUNNING (a cada {interval}s, teto {start_to}s)")
    deadline = time.monotonic() + start_to
    attempt = 0
    last_status = "?"
    while True:
        attempt += 1
        pod = _runpod_pod_status(pid, timeout=http_to)
        last_status = str(pod.get("desiredStatus") or "?")
        has_runtime = bool(pod.get("runtime"))
        _log(f"status #{attempt}: {last_status!r} (runtime {'up' if has_runtime else 'ainda não'})")

        if last_status == "RUNNING" and has_runtime:
            proxy_url = _runpod_proxy_url(pid)
            _log(f"pod RUNNING | proxy: {proxy_url}")
            return proxy_url

        if time.monotonic() >= deadline:
            raise GpuError(
                f"pod não ficou RUNNING dentro de {start_to}s "
                f"(último status: {last_status!r}, {attempt} tentativas). "
                f"O pod pode ter ficado LIGADO — rode stop_gpu() para garantir."
            )
        time.sleep(interval)


def stop_gpu(
    *,
    pod_id: str | None = None,
    http_timeout: float | None = None,
) -> None:
    """
    Desliga o pod GPU DIRETO na API do RunPod (mutation podStop). Idempotente
    do lado do RunPod.

    Lança
    -----
    GpuError
        RUNPOD_API_KEY/RUNPOD_POD_ID ausentes, rede/timeout, erro da API do
        RunPod.
    """
    pid = _runpod_pod_id(pod_id)
    http_to = _resolve_http_timeout(http_timeout)
    _log(f"RunPod → podStop({pid})")
    _runpod_graphql(
        "mutation { podStop(input: {podId: %s}) { id desiredStatus } }" % json.dumps(pid),
        timeout=http_to,
    )
    _log("stop enviado")


# --- API pública: inferência ----------------------------------------


def generate_lipsync(audio_path: str, image_path: str, pod_url: str) -> dict:
    """
    Gera o vídeo de lip-sync do MuseTalk rodando no pod GPU.

    DOIS CAMINHOS, escolhidos por env (ver docstring do módulo):
      - GPU_LIPSYNC_REAL=true → chama de verdade POST {servidor}/lipsync.
        `servidor` = pod_url (se não-vazio) senão GPU_SERVER_URL.
      - senão, GPU_LIPSYNC_MOCK=true (padrão) → STUB, sem rede.
      - senão (mock=false e real!=true) → NotImplementedError de propósito.
    O modo REAL tem precedência sobre o MOCK.

    Parâmetros
    ----------
    audio_path : str
        Caminho local do áudio (voz sintetizada) a casar com os lábios.
    image_path : str
        Caminho local da imagem/vídeo-base do apresentador.
    pod_url : str
        URL de proxy pública devolvida por start_gpu(). No modo real, pode ser
        "" para cair no GPU_SERVER_URL do env.

    Retorno
    -------
    dict
        Modo STUB: {"stub": True, ..., "output_path": None, "note": <str>}.
        Modo REAL: {"stub": False, "real": True, "server_url": <str>,
                    "output_path": <path do mp4 gerado>, ...}.

    Lança
    -----
    GpuError
        se algum dos arquivos de entrada não existe; no modo real, se a URL do
        servidor não resolve, ou em qualquer falha de rede/HTTP/resposta.
        No modo stub, também se pod_url vier vazio.
    NotImplementedError
        se GPU_LIPSYNC_MOCK == "false" e GPU_LIPSYNC_REAL != "true".
    """
    for label, path in (("audio_path", audio_path), ("image_path", image_path)):
        if not path or not os.path.isfile(path):
            raise GpuError(f"{label} não aponta para um arquivo existente: {path!r}")

    # Modo REAL tem precedência sobre o MOCK: é o opt-in explícito.
    if _real_enabled():
        return _generate_lipsync_real(audio_path, image_path, pod_url)

    if not _mock_enabled():
        raise NotImplementedError(
            "generate_lipsync(): modo real desligado (GPU_LIPSYNC_REAL != true) e mock "
            "desligado (GPU_LIPSYNC_MOCK=false). Ligue um dos dois."
        )

    if not pod_url or not str(pod_url).strip():
        raise GpuError("pod_url vazio: chame start_gpu() e passe a URL retornada.")

    # ---- STUB (inalterado) ----------------------------------------------
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
        "note": "STUB: GPU_LIPSYNC_MOCK ligado; ver GPU_LIPSYNC_REAL para o caminho real.",
    }


def _generate_lipsync_real(audio_path: str, image_path: str, pod_url: str) -> dict:
    """Caminho REAL de generate_lipsync(): POST {servidor}/lipsync."""
    server = _resolve_gpu_server_url(pod_url)
    timeout = _resolve_inference_timeout(None)
    out_path = os.path.splitext(audio_path)[0] + ".lipsync.mp4"

    _log(
        f"MODO REAL — POST {server}{_LIPSYNC_PATH} "
        f"(audio={audio_path}, image={image_path}, timeout {timeout}s)"
    )
    result_path = _post_inference(
        server,
        _LIPSYNC_PATH,
        # CONFIRME: nomes dos campos multipart esperados pelo servidor MuseTalk.
        files={"audio": audio_path, "image": image_path},
        out_path=out_path,
        timeout=timeout,
    )
    size = os.path.getsize(result_path) if os.path.isfile(result_path) else 0
    _log(f"MODO REAL — lip-sync salvo em {result_path} ({size} bytes)")
    return {
        "stub": False,
        "real": True,
        "server_url": server,
        "pod_url": pod_url or "",
        "audio_path": audio_path,
        "image_path": image_path,
        "output_path": result_path,
        "note": "REAL: resposta do servidor de inferência MuseTalk.",
    }


def tts(text: str, *, out_path: str | None = None, server_url: str | None = None) -> str:
    """
    Text-to-speech no servidor de inferência: POST {servidor}/tts.
    Só roda com GPU_LIPSYNC_REAL=true. Devolve o caminho do áudio gerado.

    CONFIRMADO (matriz 2026-09-05): corpo JSON {"text": ...}, resposta
    audio/wav (24 kHz mono). ATENÇÃO: o Chatterbox tem teto de duração —
    texto que geraria mais de ~40 s é cortado, e comentário inteiro
    (~150 s) trunca pra ~20-30 s ou crasha 500. Para texto longo use
    tts_chunked(), não esta função direto.
    """
    if not _real_enabled():
        raise GpuError(
            "tts(): modo real desligado. Defina GPU_LIPSYNC_REAL=true para chamar "
            "o servidor de inferência."
        )
    if not text or not str(text).strip():
        raise GpuError("tts(): texto vazio.")
    server = _resolve_gpu_server_url(server_url)
    timeout = _resolve_inference_timeout(None)
    out = out_path or os.path.join(tempfile.gettempdir(), "gpu_tts.wav")

    _log(f"MODO REAL — POST {server}{_TTS_PATH} ({len(str(text))} chars, timeout {timeout}s)")
    return _post_inference(
        server,
        _TTS_PATH,
        json_body={"text": str(text)},
        out_path=out,
        timeout=timeout,
    )


# --- TTS com chunking por frase (contorna o teto do Chatterbox) ---------
# O Chatterbox não segmenta: manda o texto inteiro pro modelo. A matriz de
# 2026-09-05 mostrou que só sai completo com <= ~450 chars por chamada
# (ratio dur_real/esperada 0.8-1.07); acima disso corta em 40 s exatos, e
# um comentário de notícia (~1700 chars) trunca pra ~25 s. tts_chunked()
# divide o texto em blocos de frases <= MUSETALK_TTS_MAX_CHARS (default 350,
# com margem sobre os 450), faz um POST /tts por bloco e concatena os WAVs
# num único 24 kHz mono. O gate de QA de renderer/musetalk.py roda depois,
# sobre o WAV concatenado.
DEFAULT_TTS_MAX_CHARS = 350
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…;:])\s+")
_COMMA_SPLIT_RE = re.compile(r"(?<=,)\s+")


def _chunk_text_for_tts(text: str, max_chars: int) -> list[str]:
    """Divide `text` em blocos de <= max_chars respeitando fronteira de frase.
    Frases sozinhas maiores que max_chars são sub-quebradas por vírgula e, em
    último caso, por espaço."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []

    pieces: list[str] = []
    for sent in _SENT_SPLIT_RE.split(text):
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) <= max_chars:
            pieces.append(sent)
            continue
        for sub in _COMMA_SPLIT_RE.split(sent):
            sub = sub.strip()
            while len(sub) > max_chars:
                cut = sub.rfind(" ", 0, max_chars)
                if cut <= 0:
                    cut = max_chars
                pieces.append(sub[:cut].strip())
                sub = sub[cut:].strip()
            if sub:
                pieces.append(sub)

    chunks: list[str] = []
    cur = ""
    for pc in pieces:
        if not cur:
            cur = pc
        elif len(cur) + 1 + len(pc) <= max_chars:
            cur = f"{cur} {pc}"
        else:
            chunks.append(cur)
            cur = pc
    if cur:
        chunks.append(cur)
    return chunks


def _concat_wavs(wav_paths: list[str], out_path: str) -> str:
    """Concatena WAVs num único 24 kHz mono s16le via ffmpeg. 1 entrada só é
    apenas re-amostrada. Levanta GpuError em falha."""
    if not wav_paths:
        raise GpuError("_concat_wavs: nada a concatenar")
    if len(wav_paths) == 1:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", wav_paths[0],
               "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", out_path]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for w in wav_paths:
            cmd += ["-i", w]
        n = len(wav_paths)
        filt = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]"
        cmd += ["-filter_complex", filt, "-map", "[a]",
                "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise GpuError(f"concat de WAV falhou (rc={r.returncode}): {r.stderr[-800:]}")
    return out_path


def tts_chunked(text: str, *, out_path: str, max_chars: int | None = None,
                server_url: str | None = None) -> str:
    """TTS de texto longo: divide em blocos de frases, um POST /tts por bloco,
    concatena num WAV 24 kHz mono em `out_path`. Devolve `out_path`.

    max_chars: default env MUSETALK_TTS_MAX_CHARS ou DEFAULT_TTS_MAX_CHARS."""
    if not _real_enabled():
        raise GpuError("tts_chunked(): GPU_LIPSYNC_REAL != true.")
    mc = int(max_chars or os.environ.get("MUSETALK_TTS_MAX_CHARS", "") or DEFAULT_TTS_MAX_CHARS)
    chunks = _chunk_text_for_tts(str(text), mc)
    if not chunks:
        raise GpuError("tts_chunked(): texto vazio.")
    server = _resolve_gpu_server_url(server_url)
    _log(f"tts_chunked: {len(str(text))} chars -> {len(chunks)} bloco(s) (<= {mc} ch/bloco)")

    tmpdir = tempfile.mkdtemp(prefix="tts_chunks_")
    parts: list[str] = []
    try:
        for i, ch in enumerate(chunks):
            pth = os.path.join(tmpdir, f"{i:03d}.wav")
            _log(f"  bloco {i + 1}/{len(chunks)} ({len(ch)} ch)")
            tts(ch, out_path=pth, server_url=server)
            if not os.path.isfile(pth) or os.path.getsize(pth) == 0:
                raise GpuError(f"tts_chunked: bloco {i} não gerou áudio")
            parts.append(pth)
        _concat_wavs(parts, out_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    _log(f"tts_chunked: WAV concatenado -> {out_path} ({os.path.getsize(out_path)} B)")
    return out_path


def lipsync_audio(audio_path: str, *, out_path: str | None = None,
                  server_url: str | None = None) -> str:
    """Lip-sync a partir de um WAV pronto: POST {servidor}/lipsync. O áudio já
    vem do tts_chunked(), então NÃO se passa texto. Devolve o caminho do MP4.

    CONFIRME: o corpo exato do /lipsync deste gateway (inference_server/main.py)
    não foi capturado. Tentativa: JSON {"audio_b64": <wav em base64>}. Se der
    422, o campo é outro — ajustar na 1ª sessão de pod (fetch /openapi.json).
    """
    if not _real_enabled():
        raise GpuError("lipsync_audio(): GPU_LIPSYNC_REAL != true.")
    if not audio_path or not os.path.isfile(audio_path):
        raise GpuError(f"lipsync_audio(): áudio inexistente: {audio_path!r}")
    server = _resolve_gpu_server_url(server_url)
    timeout = _resolve_generate_timeout(None)  # lip-sync é lento como /generate
    out = out_path or os.path.join(tempfile.gettempdir(), "gpu_lipsync.mp4")
    with open(audio_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    _log(
        f"MODO REAL — POST {server}{_LIPSYNC_PATH} "
        f"(wav {os.path.getsize(audio_path)} B, timeout {timeout:g}s)"
    )
    return _post_inference(
        server,
        _LIPSYNC_PATH,
        json_body={"audio_b64": b64},  # CONFIRME
        out_path=out,
        timeout=timeout,
    )


def generate_video(
    text: str,
    *,
    out_path: str | None = None,
    seed: int | None = None,
    server_url: str | None = None,
) -> dict:
    """
    Endpoint COMBINADO do servidor de inferência: POST {servidor}/generate.
    Manda só o texto; o servidor roda TTS → lip-sync com o vídeo idle FIXO do
    pod e devolve o MP4 final nos bytes do corpo (SEM JSON). É o que o pipeline
    chama — economiza a ida e volta de buscar o áudio no meio.

    DOIS CAMINHOS, escolhidos por env (igual generate_lipsync):
      - GPU_LIPSYNC_REAL=true → chama de verdade; grava o MP4 e devolve
        {"stub": False, "real": True, "output_path": <mp4>, "bytes": <int>, ...}.
      - senão, GPU_LIPSYNC_MOCK=true (padrão) → STUB, sem rede:
        {"stub": True, "output_path": None, ...}.
      - senão (mock=false e real!=true) → NotImplementedError de propósito.
    REAL tem precedência sobre MOCK.

    Parâmetros
    ----------
    text : str
        Texto da notícia a locutar. Vazio → GpuError (nos dois modos, pra pegar
        o erro cedo).
    out_path : str | None
        Onde gravar o MP4. Default: <tmp>/gpu_generate.mp4.
    seed : int | None
        Semente opcional; vai no corpo como {"seed": <int|null>}.
    server_url : str | None
        Sobrescreve GPU_SERVER_URL (ex.: a pod_url que start_gpu() devolve).

    Lança
    -----
    GpuError
        texto vazio; URL do servidor não resolve; rede/timeout; HTTP de erro
        (401 chave, 400/422 validação, 5xx worker); corpo vazio ou não-MP4.
    NotImplementedError
        GPU_LIPSYNC_MOCK=false e GPU_LIPSYNC_REAL!=true.
    """
    if not text or not str(text).strip():
        raise GpuError("generate_video(): texto vazio.")

    # Modo REAL tem precedência sobre o MOCK: é o opt-in explícito.
    if _real_enabled():
        return _generate_video_real(
            str(text), seed=seed, out_path=out_path, server_url=server_url
        )

    if not _mock_enabled():
        raise NotImplementedError(
            "generate_video(): modo real desligado (GPU_LIPSYNC_REAL != true) e mock "
            "desligado (GPU_LIPSYNC_MOCK=false). Ligue um dos dois."
        )

    _log(
        "generate_video() em MODO STUB — nenhuma chamada ao pod. "
        f"chars={len(str(text))} seed={seed}"
    )
    return {
        "stub": True,
        "real": False,
        "server_url": (server_url or os.environ.get("GPU_SERVER_URL", "") or "").strip(),
        "text_chars": len(str(text)),
        "seed": seed,
        "output_path": None,
        "note": "STUB: GPU_LIPSYNC_MOCK ligado; ver GPU_LIPSYNC_REAL para o caminho real.",
    }


def _generate_video_real(
    text: str,
    *,
    seed: int | None,
    out_path: str | None,
    server_url: str | None,
) -> dict:
    """Caminho REAL de generate_video(): POST {servidor}/generate, resposta MP4 binária."""
    server = _resolve_gpu_server_url(server_url)
    timeout = _resolve_generate_timeout(None)
    out = out_path or os.path.join(tempfile.gettempdir(), "gpu_generate.mp4")
    body = {"text": text, "seed": seed}

    _log(
        f"MODO REAL — POST {server}{_GENERATE_PATH} "
        f"({len(text)} chars, seed={seed}, timeout {timeout:g}s)"
    )
    t0 = time.monotonic()
    result_path = _post_generate(server, body, out_path=out, timeout=timeout)
    dt = time.monotonic() - t0
    size = os.path.getsize(result_path) if os.path.isfile(result_path) else 0
    _log(f"MODO REAL — vídeo salvo em {result_path} ({size} bytes) em {dt:.1f}s")
    return {
        "stub": False,
        "real": True,
        "server_url": server,
        "text_chars": len(text),
        "seed": seed,
        "output_path": result_path,
        "bytes": size,
        "elapsed_sec": round(dt, 1),
        "note": "REAL: MP4 combinado (TTS + lip-sync) do servidor de inferência.",
    }


# --- Ciclo completo com stop garantido -------------------------------


def _wait_inference_ready(pod_url: str, *, timeout: float | None = None) -> None:
    """
    RunPod RUNNING só confirma o container de pé. O gateway de inferência sobe
    rápido, mas os workers (Chatterbox/TTS e MuseTalk/lip-sync) são processos
    separados que ainda precisam carregar modelo — e o `/health` do gateway
    responde 200 mesmo com os workers mortos (check raso; foi o que deixou o
    renderer mandar trabalho pra um pod meio-morto em 2026-09-04).

    Por isso o polling é em `/ready`, que só devolve 200 quando os DOIS
    workers estão de pé; enquanto não, dá 503 com o status de cada um. Poll a
    cada 3s até 200 ou estourar `timeout` (GPU_READY_TIMEOUT_SEC, padrão
    120s). Em timeout a GpuError carrega o último corpo do `/ready` — que diz
    QUAL worker não subiu.
    """
    to = _resolve_float_env("GPU_READY_TIMEOUT_SEC", DEFAULT_READY_TIMEOUT_SEC, timeout)
    deadline = time.monotonic() + to
    last = "sem resposta"
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{pod_url}/ready", timeout=10)
            if resp.status_code == 200:
                return
            body = " ".join((resp.text or "").split())
            last = f"HTTP {resp.status_code}" + (f" {body[:400]}" if body else "")
        except requests.exceptions.RequestException as e:
            last = str(e)
        time.sleep(3)
    raise GpuError(
        f"servidor de inferência não ficou pronto em {to:.0f}s após o pod "
        f"RUNNING ({pod_url}) — último /ready: {last}"
    )


@contextmanager
def gpu_session(
    *,
    pod_id: str | None = None,
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
        pod_id=pod_id,
        http_timeout=http_timeout,
        start_timeout=start_timeout,
        poll_interval=poll_interval,
    )
    t_ready = time.monotonic()
    _log(f"pod RUNNING (RunPod) em {t_ready - t0:.1f}s")
    _wait_inference_ready(pod_url)
    _log(f"servidor de inferência pronto em {time.monotonic() - t0:.1f}s total")
    try:
        yield pod_url
    finally:
        try:
            stop_gpu(pod_id=pod_id, http_timeout=http_timeout)
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
#   # ciclo completo REAL do /generate (start → generate_video → stop):
#   #   gasta GPU de verdade. .env já traz GPU_SERVER_URL + INFERENCE_SERVER_API_KEY.
#   python3 lib/gpu_client.py --real --generate --text "Boa noite. A notícia de hoje..."
#
#   # PRIMEIRO teste real do /generate, SEM ligar/desligar o pod (pod já de pé):
#   python3 lib/gpu_client.py --real --no-lifecycle --generate --text "texto curto"
#
#   # ciclo completo (start → lipsync stub → stop), stop garantido:
#   RUNPOD_API_KEY=... RUNPOD_POD_ID=... \
#     python3 lib/gpu_client.py --audio /caminho/voz.mp3 --image /caminho/rosto.png
#
#   # teste real do /lipsync, SEM ligar/desligar o pod (pod já de pé):
#   GPU_SERVER_URL=https://<pod>-8000.proxy.runpod.net \
#     python3 lib/gpu_client.py --real --no-lifecycle \
#       --audio /caminho/voz.mp3 --image /caminho/rosto.png
#
#   # só ligar e desligar, sem inferência:
#   python3 lib/gpu_client.py --skip-lipsync --pod-id ykvbdr0tnvzjqk
#
#   # só desligar (pânico: "liguei e o teste morreu"):
#   python3 lib/gpu_client.py --stop-only
def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Teste manual do ciclo de vida do pod GPU (direto na API do RunPod) "
        "e das chamadas ao servidor de inferência."
    )
    parser.add_argument("--pod-id", default=None, help="Sobrescreve RUNPOD_POD_ID.")
    parser.add_argument(
        "--gpu-server-url",
        default=None,
        help="Sobrescreve GPU_SERVER_URL (servidor de inferência dentro do pod).",
    )
    parser.add_argument("--audio", default=None, help="Caminho do áudio para o lip-sync.")
    parser.add_argument("--image", default=None, help="Caminho da imagem-base para o lip-sync.")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Chama o endpoint COMBINADO generate_video() (POST /generate) em vez "
        "de generate_lipsync(). Exige --text.",
    )
    parser.add_argument("--text", default=None, help="Texto da notícia para --generate.")
    parser.add_argument(
        "--seed", type=int, default=None, help="Semente opcional para --generate (default: null)."
    )
    parser.add_argument("--out", default=None, help="Caminho de saída do MP4 (--generate).")
    parser.add_argument(
        "--real",
        action="store_true",
        help="Liga GPU_LIPSYNC_REAL=true só nesta execução (chama o servidor de verdade).",
    )
    parser.add_argument(
        "--no-lifecycle",
        action="store_true",
        help="NÃO liga/desliga o pod: chama a inferência direto contra "
        "--gpu-server-url/GPU_SERVER_URL (pod já de pé). Use no 1º teste real.",
    )
    parser.add_argument(
        "--skip-lipsync",
        action="store_true",
        help="Faz só start → stop, sem chamar nenhuma inferência.",
    )
    parser.add_argument(
        "--stop-only",
        action="store_true",
        help="Só chama stop_gpu() e sai (para desligar um pod esquecido ligado).",
    )
    parser.add_argument("--http-timeout", type=float, default=None, help="Timeout de cada request HTTP à API do RunPod (s).")
    parser.add_argument("--start-timeout", type=float, default=None, help="Teto do boot do pod (s).")
    parser.add_argument("--poll-interval", type=float, default=None, help="Intervalo entre polls de status (s).")
    args = parser.parse_args(argv)

    if args.real:
        os.environ["GPU_LIPSYNC_REAL"] = "true"
    if args.gpu_server_url:
        os.environ["GPU_SERVER_URL"] = args.gpu_server_url

    mode = "REAL" if _real_enabled() else ("STUB/MOCK" if _mock_enabled() else "NotImplemented")
    _log(f"modo de inferência: {mode}")

    try:
        if args.stop_only:
            stop_gpu(pod_id=args.pod_id, http_timeout=args.http_timeout)
            return 0

        # Caminho do endpoint COMBINADO /generate (o que o pipeline vai usar).
        if args.generate:
            if not args.text or not args.text.strip():
                parser.error("--generate exige --text não-vazio.")
            if args.no_lifecycle:
                result = generate_video(
                    args.text, out_path=args.out, seed=args.seed,
                    server_url=os.environ.get("GPU_SERVER_URL", ""),
                )
                _log(f"generate_video → {json.dumps(result, ensure_ascii=False)}")
                return 0
            with gpu_session(
                pod_id=args.pod_id,
                http_timeout=args.http_timeout,
                start_timeout=args.start_timeout,
                poll_interval=args.poll_interval,
            ) as pod_url:
                result = generate_video(
                    args.text, out_path=args.out, seed=args.seed, server_url=pod_url
                )
                _log(f"generate_video → {json.dumps(result, ensure_ascii=False)}")
            return 0

        do_lipsync = not args.skip_lipsync
        if do_lipsync and (not args.audio or not args.image):
            parser.error("--audio e --image são obrigatórios (ou passe --skip-lipsync).")

        if args.no_lifecycle:
            if not do_lipsync:
                parser.error("--no-lifecycle só faz sentido com --audio/--image (sem --skip-lipsync).")
            result = generate_lipsync(args.audio, args.image, os.environ.get("GPU_SERVER_URL", ""))
            _log(f"generate_lipsync → {json.dumps(result, ensure_ascii=False)}")
            return 0

        with gpu_session(
            pod_id=args.pod_id,
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
