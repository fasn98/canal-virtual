"""Tradução automática de texto via API da DeepL.

Usado no início do pipeline (classifier) para traduzir o título das notícias
para o idioma-alvo do canal (TARGET_LANGUAGE) antes de qualquer estágio que
gere tela ou áudio.

PRINCÍPIO: degradação graciosa. Qualquer falha (sem chave configurada, erro de
rede, limite de caracteres excedido, resposta inesperada) é logada de forma
clara e a função devolve o TEXTO ORIGINAL sem levantar exceção. O canal ao vivo
nunca trava por causa da tradução — no pior caso a manchete sai em inglês.
"""

import os
import sys

import requests

DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "").strip()
# Código de idioma no formato que a DeepL espera como target_lang.
# "PT-BR" = português do Brasil (confirmado na doc atual da DeepL).
# Pode variar por instância/canal no futuro.
TARGET_LANGUAGE = os.environ.get("TARGET_LANGUAGE", "PT-BR").strip() or "PT-BR"
DEEPL_TIMEOUT_SEC = int(os.environ.get("DEEPL_TIMEOUT_SEC", "10"))

# Fontes cujo conteúdo JÁ chega no idioma-alvo do canal quando o canal é PT.
# Para essas o classifier PULA a chamada à DeepL: economiza cota do plano
# gratuito e evita que a DeepL "reescreva" um texto que já está correto em
# PT-BR. Rótulos batem com o campo `source` do collector ("Agência Brasil").
# Sobrescrevível via env PT_NATIVE_SOURCES (lista separada por vírgula).
PT_NATIVE_SOURCES = {
    s.strip().lower()
    for s in (os.environ.get("PT_NATIVE_SOURCES") or "Agência Brasil").split(",")
    if s.strip()
}

TAG = "Translator"


def is_already_target_language(source):
    """True quando uma notícia da fonte `source` já vem no idioma-alvo e não há
    o que traduzir. Hoje: fontes PT-nativas (PT_NATIVE_SOURCES) quando
    TARGET_LANGUAGE é português. Se o canal for para outro idioma, mesmo essas
    fontes voltam a ser traduzidas normalmente."""
    if not source:
        return False
    if not TARGET_LANGUAGE.upper().startswith("PT"):
        return False
    return source.strip().lower() in PT_NATIVE_SOURCES


def _api_base(key):
    """Chaves do plano gratuito terminam em ':fx' e usam o host api-free.
    As do plano pago usam api.deepl.com. Detecta automaticamente."""
    return "https://api-free.deepl.com" if key.endswith(":fx") else "https://api.deepl.com"


def translate_text(text, target_lang=None):
    """Traduz `text` para `target_lang` (default: TARGET_LANGUAGE) via DeepL.

    Retorna sempre uma string:
      - o texto traduzido em caso de sucesso;
      - o `text` original (inalterado) em qualquer cenário de falha.
    Nunca levanta exceção.
    """
    target_lang = (target_lang or TARGET_LANGUAGE).strip()
    text = text or ""

    if not text.strip():
        return text

    if not DEEPL_API_KEY:
        print(
            f"{TAG} → DEEPL_API_KEY não configurada; mantendo texto original: {text!r}",
            flush=True,
        )
        return text

    url = f"{_api_base(DEEPL_API_KEY)}/v2/translate"

    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}"},
            data={"text": text, "target_lang": target_lang},
            timeout=DEEPL_TIMEOUT_SEC,
        )
    except requests.exceptions.RequestException as e:
        print(
            f"{TAG} → ERRO DE REDE na DeepL, mantendo texto original ({e}): {text!r}",
            flush=True,
        )
        return text

    if resp.status_code != 200:
        # 403 = chave inválida | 429 = rate limit | 456 = cota mensal esgotada
        print(
            f"{TAG} → ERRO DeepL (status={resp.status_code}), mantendo texto original. "
            f"Resposta: {resp.text[:200]!r} | original={text!r}",
            flush=True,
        )
        return text

    try:
        translations = resp.json().get("translations", [])
        translated = (translations[0].get("text") or "").strip()
    except (ValueError, IndexError, AttributeError, KeyError) as e:
        print(
            f"{TAG} → RESPOSTA inesperada da DeepL, mantendo texto original ({e}): {text!r}",
            flush=True,
        )
        return text

    if not translated:
        print(
            f"{TAG} → DeepL retornou texto vazio, mantendo texto original: {text!r}",
            flush=True,
        )
        return text

    print(f"{TAG} → OK [{target_lang}] {text!r} → {translated!r}", flush=True)
    return translated


if __name__ == "__main__":
    # Uso: python translate.py "some english headline"
    sample = " ".join(sys.argv[1:]) or "Board of Peace's Gaza envoy criticises Israeli strikes"
    print(translate_text(sample))
