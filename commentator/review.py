"""Revisão editorial do comentário antes de liberar para o áudio.

Três chamadas SEPARADAS ao Claude, cada uma com um papel estreito, rodadas
entre a geração do comentário (commentator) e o synthesizer:

  1. Verificador de fatos  — o comentário inventou número/nome/citação/afirmação
     que não estava no título/resumo da fonte?
  2. Revisor editorial     — tom, viés não-intencional, opinião disfarçada de fato?
  3. Aprovador final        — recebe os dois vereditos e decide LIBERADO / BLOQUEADO.

Contrato de retorno de `review_commentary()`:
  (True,  "")      -> LIBERADO, segue para o synthesizer
  (False, motivo)  -> BLOQUEADO por conteúdo; o commentator deve gerar uma
                      versão corrigida usando `motivo` (até MAX_CORRECTION_ATTEMPTS).

Erros de infraestrutura (API fora, resposta ilegível) levantam
`ReviewUnavailable` — o commentator NÃO publica e NÃO descarta: deixa a
mensagem pendente para o reprocessamento automático tentar de novo mais tarde.
"""

import datetime
import json
import os
import re

import redis

TAG = "Commentator/Revisão"

# --- Contadores para a metrics-api (best-effort) ---------------------------
# Cada chamada de revisão bem-sucedida ao Claude incrementa
# `metrics:claude_calls:<tipo>:<data>` (tipo = fact_check | editorial |
# final_approval). Redis próprio, socket curto; qualquer falha é ignorada e
# não afeta a revisão.
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

# Liga/desliga a etapa inteira. Desligado = comportamento antigo (publica o
# comentário direto, sem revisão).
REVIEW_ENABLED = os.environ.get("ENABLE_EDITORIAL_REVIEW", "true").strip().lower() in (
    "1", "true", "yes", "on",
)
# Tentativas de CORREÇÃO após a 1ª reprovação. 2 = gera, revisa; se reprovar,
# corrige (1), revisa; se reprovar, corrige (2), revisa; se ainda reprovar,
# descarta o item.
MAX_CORRECTION_ATTEMPTS = int(os.environ.get("MAX_CORRECTION_ATTEMPTS", "2"))
# Modelo das revisões (por padrão o mesmo do comentário).
REVIEW_MODEL = (
    os.environ.get("REVIEW_MODEL", "").strip()
    or os.environ.get("ANTHROPIC_MODEL", "").strip()
    or "claude-sonnet-4-5"
)
REVIEW_MAX_TOKENS = int(os.environ.get("REVIEW_MAX_TOKENS", "400"))


class ReviewUnavailable(Exception):
    """Falha de infraestrutura na revisão (API indisponível / resposta
    ilegível). Sinaliza 'tente de novo depois', não 'reprovado'."""


def _extract_text(resp):
    return "\n".join(
        b.text.strip()
        for b in resp.content
        if getattr(b, "type", None) == "text" and b.text.strip()
    ).strip()


def _ask_verdict(client, system, user, kind):
    """Faz uma chamada de revisão e devolve (aprovado: bool, motivo: str).
    Espera um JSON {"veredito": "APROVADO"|"REPROVADO", "motivo": "..."}.
    Levanta ReviewUnavailable se a chamada falhar ou a resposta não for
    interpretável. `kind` rotula o contador da metrics-api."""
    try:
        resp = client.messages.create(
            model=REVIEW_MODEL,
            max_tokens=REVIEW_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw = _extract_text(resp)
    except Exception as e:
        raise ReviewUnavailable(f"{type(e).__name__}: {e}")

    _bump_metric(f"claude_calls:{kind}")

    if not raw:
        raise ReviewUnavailable("resposta vazia")

    data = _parse_json_obj(raw)
    if data is None:
        raise ReviewUnavailable(f"resposta não-JSON: {raw[:200]!r}")

    veredito = str(data.get("veredito", "")).strip().upper()
    motivo = str(data.get("motivo", "")).strip()
    if veredito not in ("APROVADO", "REPROVADO"):
        raise ReviewUnavailable(f"veredito inesperado: {veredito!r}")

    return veredito == "APROVADO", motivo


def _parse_json_obj(raw):
    """Tenta json.loads direto; se falhar, extrai o primeiro {...} do texto."""
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# --- 1) Verificador de fatos -------------------------------------------------

_FACT_SYSTEM = (
    "Você é um verificador de fatos rigoroso de uma redação de telejornal. "
    "Recebe o MATERIAL ORIGINAL de uma notícia (título e resumo de uma agência) "
    "e um COMENTÁRIO analítico que um comentarista escreveu sobre ela. "
    "Sua única tarefa: verificar se o comentário introduz algum FATO ESPECÍFICO "
    "— número, estatística, porcentagem, data, valor, nome próprio, cargo, local "
    "ou citação textual — que NÃO está presente no material original nem é "
    "dedutível dele. Contextualização, análise, interpretação, cenários "
    "hipotéticos claramente marcados como tais e conhecimento geral amplo são "
    "PERMITIDOS. Invenção de dados concretos apresentados como se fossem da "
    "notícia é REPROVAÇÃO. O material original está em inglês; o comentário, em "
    "português — divergência de idioma não é problema. "
    'Responda SOMENTE um objeto JSON: {"veredito": "APROVADO" ou "REPROVADO", '
    '"motivo": "vazio se aprovado; se reprovado, cite o trecho/dado inventado"}.'
)


def fact_check(client, commentary, title_original, summary, source):
    user = (
        f"FONTE: {source or '(não informada)'}\n"
        f"TÍTULO ORIGINAL: {title_original or '(sem título)'}\n"
        f"RESUMO ORIGINAL: {summary or '(sem resumo)'}\n\n"
        f"COMENTÁRIO A VERIFICAR:\n{commentary}"
    )
    return _ask_verdict(client, _FACT_SYSTEM, user, "fact_check")


# --- 2) Revisor editorial --------------------------------------------------

_EDITORIAL_SYSTEM = (
    "Você é o editor-chefe de um telejornal, responsável pela linha editorial. "
    "Recebe um COMENTÁRIO analítico sobre uma notícia e avalia SOMENTE: "
    "(a) tom — adequado a um telejornal sério, sem sensacionalismo, alarmismo "
    "ou apelo emocional excessivo; (b) viés não-intencional — o texto pende para "
    "um lado sem necessidade, adjetiva atores de forma desigual, ou trata uma "
    "das partes com mais benevolência; (c) opinião disfarçada de fato — juízos "
    "de valor apresentados como se fossem constatação objetiva. "
    "Análise e apontamento de cenários são esperados e OK, desde que "
    "atribuídos como interpretação. Seja exigente mas não pedante: só reprove "
    "problemas que um espectador perceberia como parcialidade ou falta de "
    "profissionalismo. "
    'Responda SOMENTE um objeto JSON: {"veredito": "APROVADO" ou "REPROVADO", '
    '"motivo": "vazio se aprovado; se reprovado, aponte o trecho e o problema"}.'
)


def editorial_review(client, commentary, title, category):
    user = (
        f"TÍTULO (pt): {title or '(sem título)'}\n"
        f"CATEGORIA: {category or '(sem categoria)'}\n\n"
        f"COMENTÁRIO A REVISAR:\n{commentary}"
    )
    return _ask_verdict(client, _EDITORIAL_SYSTEM, user, "editorial")


# --- 3) Aprovador final ---------------------------------------------------

_APPROVER_SYSTEM = (
    "Você é o aprovador final de publicação de um telejornal. Recebe os "
    "vereditos de dois revisores independentes (verificador de fatos e revisor "
    "editorial), cada um com APROVADO/REPROVADO e um motivo. Sua decisão: "
    "LIBERADO apenas se AMBOS aprovaram e não há motivo residual relevante; "
    "BLOQUEADO se qualquer um reprovou. Ao bloquear, consolide em uma frase "
    "objetiva o que o comentarista precisa corrigir. "
    'Responda SOMENTE um objeto JSON: {"veredito": "LIBERADO" ou "BLOQUEADO", '
    '"motivo": "vazio se liberado; instrução de correção se bloqueado"}.'
)


def final_approval(client, fact_ok, fact_reason, editorial_ok, editorial_reason):
    """Terceira chamada ao Claude. Se ela falhar, cai numa regra determinística
    (LIBERADO só se ambos aprovaram) — sem levantar ReviewUnavailable, porque
    aqui já temos os dois vereditos e uma decisão segura é possível."""
    user = (
        f"VERIFICADOR DE FATOS: {'APROVADO' if fact_ok else 'REPROVADO'} — "
        f"{fact_reason or '(sem ressalvas)'}\n"
        f"REVISOR EDITORIAL: {'APROVADO' if editorial_ok else 'REPROVADO'} — "
        f"{editorial_reason or '(sem ressalvas)'}"
    )
    try:
        resp = client.messages.create(
            model=REVIEW_MODEL,
            max_tokens=REVIEW_MAX_TOKENS,
            system=_APPROVER_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        _bump_metric("claude_calls:final_approval")
        data = _parse_json_obj(_extract_text(resp))
        if data is not None:
            veredito = str(data.get("veredito", "")).strip().upper()
            motivo = str(data.get("motivo", "")).strip()
            if veredito in ("LIBERADO", "BLOQUEADO"):
                return veredito == "LIBERADO", motivo
    except Exception as e:
        print(f"{TAG} → Aprovador final: chamada falhou ({type(e).__name__}: {e}); "
              "usando regra determinística.", flush=True)

    if fact_ok and editorial_ok:
        return True, ""
    partes = []
    if not fact_ok:
        partes.append(f"verificação de fatos: {fact_reason or 'reprovado'}")
    if not editorial_ok:
        partes.append(f"revisão editorial: {editorial_reason or 'reprovado'}")
    return False, "; ".join(partes)


# --- Orquestração ---------------------------------------------------------

def review_commentary(client, news_id, commentary, title, title_original,
                      summary, category, source):
    """Roda os três revisores em sequência e loga cada decisão no formato
    auditável. Retorna (liberado: bool, motivo_para_correcao: str).
    Levanta ReviewUnavailable se o verificador de fatos ou o revisor editorial
    não puderem ser consultados."""
    fact_ok, fact_reason = fact_check(client, commentary, title_original, summary, source)
    if fact_ok:
        print(f"{TAG} → Verificador de fatos → APROVADO para {news_id}", flush=True)
    else:
        print(f"{TAG} → Verificador de fatos → REPROVADO para {news_id}: {fact_reason}", flush=True)

    editorial_ok, editorial_reason = editorial_review(client, commentary, title, category)
    if editorial_ok:
        print(f"{TAG} → Revisor editorial → APROVADO para {news_id}", flush=True)
    else:
        print(f"{TAG} → Revisor editorial → REPROVADO para {news_id}: {editorial_reason}", flush=True)

    liberado, motivo = final_approval(
        client, fact_ok, fact_reason, editorial_ok, editorial_reason
    )
    if liberado:
        print(f"{TAG} → Aprovador final → LIBERADO para {news_id}", flush=True)
    else:
        print(f"{TAG} → Aprovador final → BLOQUEADO para {news_id}: {motivo}", flush=True)

    return liberado, motivo
