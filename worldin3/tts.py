"""Síntese de voz do roteiro — mesma ElevenLabs (voz, modelo e parâmetros) do
`synthesizer`, só que com um contador de orçamento PRÓPRIO para não roubar os
créditos diários das notícias.

Contrato: `synthesize(text, out_path, ignore_budget=False)` grava um mp3 em
`out_path` e devolve (out_path, chars). Levanta RuntimeError em qualquer falha
(sem fallback: um resumo sem áudio real não vai ao ar).
"""

import datetime
import os

import redis
import requests

from . import config

r = redis.Redis(host="redis", port=6379, decode_responses=True, socket_timeout=10)

_CREDITS_KEY_PREFIX = "worldin3:elevenlabs:credits_used:"
_CREDITS_TTL_SEC = 7 * 24 * 3600
_METRICS_TTL_SEC = 8 * 24 * 3600


def _today_key():
    return _CREDITS_KEY_PREFIX + datetime.date.today().isoformat()


def credits_used_today():
    try:
        v = r.get(_today_key())
        return int(v) if v else 0
    except Exception:
        return 0


def _add_credits(chars):
    try:
        key = _today_key()
        total = r.incrby(key, chars)
        r.expire(key, _CREDITS_TTL_SEC)
        # Espelho para um painel futuro; nunca interfere no fluxo.
        mkey = f"metrics:worldin3_credits_used:{datetime.date.today().isoformat()}"
        r.incrby(mkey, chars)
        r.expire(mkey, _METRICS_TTL_SEC)
        return total
    except Exception:
        return None


def synthesize(text, out_path, ignore_budget=False):
    text = (text or "").strip()
    if not text:
        raise RuntimeError("texto vazio para TTS")
    if not config.ELEVENLABS_API_KEY or not config.ELEVENLABS_VOICE_ID:
        raise RuntimeError("ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID não configurados")

    chars = len(text)
    used = credits_used_today()
    if not ignore_budget and used + chars > config.DAILY_CREDIT_BUDGET:
        raise RuntimeError(
            f"orçamento diário do formato esgotado: {used} usados + {chars} desta "
            f"chamada passaria de {config.DAILY_CREDIT_BUDGET} "
            f"(WORLDIN3_DAILY_CREDIT_BUDGET). Use --ignore-budget no preview."
        )

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": config.ELEVENLABS_MODEL_ID,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"ElevenLabs status={resp.status_code}: {resp.text[:300]}")
    with open(out_path, "wb") as f:
        f.write(resp.content)
    if os.path.getsize(out_path) == 0:
        os.remove(out_path)
        raise RuntimeError("mp3 vazio retornado pela ElevenLabs")

    total = _add_credits(chars)
    print(
        f"[worldin3/tts] áudio gerado: {out_path} ({chars} caracteres; "
        f"orçamento do dia: {total if total is not None else used + chars}/"
        f"{config.DAILY_CREDIT_BUDGET})",
        flush=True,
    )
    return out_path, chars
