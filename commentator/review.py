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
    or "claude-sonnet-5"
)
REVIEW_MAX_TOKENS = int(os.environ.get("REVIEW_MAX_TOKENS", "400"))


def log_usage(tag, kind, usage):
    """Loga os tokens de cada categoria que a API devolve, para acompanhar a
    taxa de acerto do cache de prompt ao longo do tempo. `cache_read` alto a
    partir da 2ª chamada do mesmo tipo = cache funcionando; só `cache_write` em
    toda chamada = cache não está sendo reaproveitado. Best-effort: nunca
    interrompe o pipeline."""
    try:
        print(
            f"{tag} → tokens[{kind}] "
            f"input={getattr(usage, 'input_tokens', 0)} "
            f"cache_write={getattr(usage, 'cache_creation_input_tokens', 0)} "
            f"cache_read={getattr(usage, 'cache_read_input_tokens', 0)} "
            f"output={getattr(usage, 'output_tokens', 0)}",
            flush=True,
        )
    except Exception:
        pass


class ReviewUnavailable(Exception):
    """Falha de infraestrutura na revisão (API indisponível / resposta
    ilegível). Sinaliza 'tente de novo depois', não 'reprovado'."""


def _extract_text(resp):
    return "\n".join(
        b.text.strip()
        for b in resp.content
        if getattr(b, "type", None) == "text" and b.text.strip()
    ).strip()


def _build_review_system(role_block):
    """Monta o `system` das chamadas de revisão como DOIS blocos estáticos, cada
    um com um breakpoint de cache:

      1. _REVIEW_PREAMBLE — idêntico byte a byte nas TRÊS chamadas de revisão e
         em todas as notícias. Dentro de uma mesma notícia, a 1ª revisão grava
         este bloco no cache e as outras duas o leem a ~10% do custo.
      2. role_block — as instruções do papel (verificador / editorial /
         aprovador). Como o prefixo acumulado (preâmbulo + papel) já passa do
         mínimo cacheável, este 2º breakpoint faz o system inteiro daquele papel
         ser lido do cache nas notícias seguintes (janela de 5 min).

    Estático primeiro, dinâmico (a notícia) depois, sempre — o conteúdo da
    notícia vai só na mensagem 'user', nunca aqui."""
    return [
        {
            "type": "text",
            "text": _REVIEW_PREAMBLE,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": role_block,
            "cache_control": {"type": "ephemeral"},
        },
    ]


def _ask_verdict(client, role_block, user, kind):
    """Faz uma chamada de revisão e devolve (aprovado: bool, motivo: str).
    Espera um JSON {"veredito": "APROVADO"|"REPROVADO", "motivo": "..."}.
    Levanta ReviewUnavailable se a chamada falhar ou a resposta não for
    interpretável. `kind` rotula o contador da metrics-api."""
    try:
        resp = client.messages.create(
            model=REVIEW_MODEL,
            max_tokens=REVIEW_MAX_TOKENS,
            system=_build_review_system(role_block),
            messages=[{"role": "user", "content": user}],
        )
        log_usage(TAG, kind, resp.usage)
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


# --- Preâmbulo compartilhado das 3 revisões (bloco estático cacheável) ------
# Idêntico byte a byte nas três chamadas (verificador, editorial, aprovador) e
# em todas as notícias. É o 1º breakpoint de cache do `system` — ver
# _build_review_system(). Precisa passar do mínimo cacheável do modelo (1024
# tokens no Sonnet); qualquer edição aqui invalida o cache das três revisões.
_REVIEW_PREAMBLE = (
    "CONTEXTO DA OPERAÇÃO\n"
    "\n"
    "Você faz parte da revisão editorial de um telejornal automatizado. O fluxo "
    "é o seguinte: um coletor capta notícias de agências internacionais (BBC, "
    "Guardian) em inglês; um classificador traduz o título para o português e "
    "atribui uma categoria; um comentarista escreve um comentário analítico em "
    "português do Brasil sobre a notícia; e então três revisores independentes — "
    "um verificador de fatos, um revisor editorial e um aprovador final — "
    "decidem se o comentário pode virar áudio e ir ao ar. Você é um desses três "
    "revisores. O seu papel específico está descrito logo depois deste contexto "
    "comum; leia-o com atenção, porque ele restringe aquilo que você deve "
    "avaliar. Não opine sobre aspectos que pertencem a outro revisor.\n"
    "\n"
    "O MATERIAL QUE VOCÊ RECEBE\n"
    "\n"
    "- FONTE: a agência de origem, quando informada.\n"
    "- TÍTULO ORIGINAL: o título em inglês, como veio da agência.\n"
    "- TÍTULO (pt): a tradução para o português usada no ar.\n"
    "- RESUMO ORIGINAL: o resumo em inglês da agência. Esta é a ÚNICA base "
    "factual autorizada para o comentário.\n"
    "- CATEGORIA: rótulo temático (Política, Economia, Segurança, Clima, Saúde, "
    "Entretenimento, Esportes, Mundo e afins).\n"
    "- COMENTÁRIO: o texto do comentarista, em português, que está sendo "
    "avaliado.\n"
    "\n"
    "O comentário é uma ANÁLISE sobre a notícia, não a leitura da notícia. "
    "Espera-se que ele contextualize o tema, aponte causas prováveis, "
    "implicações e cenários possíveis. Isso é a função dele, não um defeito a "
    "corrigir.\n"
    "\n"
    'O QUE É "FATO ESPECÍFICO", E POR QUE IMPORTA\n'
    "\n"
    "Fato específico é qualquer afirmação concreta e verificável atribuída à "
    "realidade: um número, uma estatística, uma porcentagem, uma data, um valor "
    "monetário, um placar, um resultado de votação, um nome próprio, um cargo, "
    "um local determinado ou uma citação textual entre aspas. Um fato "
    "específico só pode aparecer no comentário se estiver no título original, no "
    "resumo original, ou for dedução direta e inequívoca deles.\n"
    "\n"
    "NÃO são fatos específicos, e portanto são PERMITIDOS mesmo sem constar da "
    "fonte: contextualização histórica ampla; conhecimento geral consolidado "
    '("bancos centrais costumam reagir à inflação elevando os juros"); análise '
    "de tendências; cenários hipotéticos claramente marcados como possibilidade "
    '("caso o quadro se mantenha, é provável que..."); e juízos de '
    "probabilidade explicitados como interpretação do comentarista. O idioma da "
    "fonte (inglês) e o do comentário (português) são diferentes por construção "
    "— divergência de idioma nunca é um problema a apontar.\n"
    "\n"
    "CALIBRAGEM DE SEVERIDADE\n"
    "\n"
    "Seja exigente, mas não pedante. Só reprove problemas que um espectador "
    "atento perceberia como erro factual, parcialidade ou falta de "
    "profissionalismo. Não reprove por preferência de estilo, por o comentário "
    "ser mais raso ou mais profundo do que você faria, nem por ele deixar de "
    "mencionar algo que a própria fonte também não mencionava. Na dúvida entre "
    "um problema real e uma implicância, aprove.\n"
    "\n"
    "FORMATO DA RESPOSTA — OBRIGATÓRIO\n"
    "\n"
    "Responda com UM único objeto JSON e NADA MAIS: sem texto antes, sem texto "
    "depois, sem markdown, sem cercas de código. O objeto tem exatamente duas "
    "chaves:\n"
    "\n"
    '  {"veredito": "<um dos rótulos definidos no seu papel>", "motivo": "<string>"}\n'
    "\n"
    'Regras do campo "motivo": string vazia ("") quando você aprova ou libera; '
    "quando reprova ou bloqueia, uma frase objetiva citando o trecho ou o dado "
    "problemático e o que precisa mudar. Esse motivo é entregue de volta ao "
    "comentarista para a reescrita, então precisa ser acionável. Nunca mais de "
    "duas frases.\n"
    "\n"
    "EXEMPLOS DO FORMATO (ilustrativos, não são o caso em avaliação)\n"
    "\n"
    "Aprovação:\n"
    '  {"veredito": "APROVADO", "motivo": ""}\n'
    "\n"
    "Reprovação por dado inventado:\n"
    '  {"veredito": "REPROVADO", "motivo": "O comentário afirma que a medida foi '
    "'aprovada por 7 votos a 2', placar de votação que não aparece no resumo da "
    'fonte."}\n'
)

# --- 1) Verificador de fatos -------------------------------------------------

_FACT_ROLE = (
    "SEU PAPEL: VERIFICADOR DE FATOS\n"
    "\n"
    "Sua única tarefa é checar se o COMENTÁRIO introduz algum FATO ESPECÍFICO "
    "(conforme a definição do preâmbulo) que NÃO está no material original nem "
    "é dedutível dele. Tom, viés e estilo NÃO são seu departamento — ignore-os "
    "aqui. Contextualização, análise, interpretação, cenários hipotéticos "
    "marcados como tais e conhecimento geral amplo são PERMITIDOS. Inventar "
    "dado concreto e apresentá-lo como se fosse da notícia é REPROVAÇÃO.\n"
    "\n"
    'Rótulos do "veredito" no seu papel: "APROVADO" ou "REPROVADO". Se '
    "REPROVADO, o motivo deve citar o trecho ou o dado inventado."
)


def fact_check(client, commentary, title_original, summary, source):
    user = (
        f"FONTE: {source or '(não informada)'}\n"
        f"TÍTULO ORIGINAL: {title_original or '(sem título)'}\n"
        f"RESUMO ORIGINAL: {summary or '(sem resumo)'}\n\n"
        f"COMENTÁRIO A VERIFICAR:\n{commentary}"
    )
    return _ask_verdict(client, _FACT_ROLE, user, "fact_check")


# --- 2) Revisor editorial --------------------------------------------------

_EDITORIAL_ROLE = (
    "SEU PAPEL: REVISOR EDITORIAL (EDITOR-CHEFE)\n"
    "\n"
    "Você é responsável pela linha editorial. Avalie SOMENTE: (a) tom — "
    "adequado a um telejornal sério, sem sensacionalismo, alarmismo ou apelo "
    "emocional excessivo; (b) viés não-intencional — o texto pende para um lado "
    "sem necessidade, adjetiva atores de forma desigual, ou trata uma das "
    "partes com mais benevolência que a outra; (c) opinião disfarçada de fato — "
    "juízo de valor apresentado como se fosse constatação objetiva. Análise e "
    "apontamento de cenários são esperados e estão OK, desde que atribuídos "
    "como interpretação do comentarista. Checagem factual NÃO é seu "
    "departamento — outro revisor cuida disso.\n"
    "\n"
    'Rótulos do "veredito" no seu papel: "APROVADO" ou "REPROVADO". Se '
    "REPROVADO, o motivo deve apontar o trecho e qual dos três problemas ele "
    "configura."
)


def editorial_review(client, commentary, title, category):
    user = (
        f"TÍTULO (pt): {title or '(sem título)'}\n"
        f"CATEGORIA: {category or '(sem categoria)'}\n\n"
        f"COMENTÁRIO A REVISAR:\n{commentary}"
    )
    return _ask_verdict(client, _EDITORIAL_ROLE, user, "editorial")


# --- 3) Aprovador final ---------------------------------------------------

_APPROVER_ROLE = (
    "SEU PAPEL: APROVADOR FINAL\n"
    "\n"
    "Você não relê o comentário: recebe os vereditos dos dois revisores "
    "independentes (verificador de fatos e revisor editorial), cada um com "
    "APROVADO ou REPROVADO e um motivo. Sua decisão: LIBERADO apenas se AMBOS "
    "aprovaram e não há motivo residual relevante; BLOQUEADO se qualquer um "
    "reprovou. Ao bloquear, consolide numa única frase objetiva, dirigida ao "
    "comentarista, o que ele precisa corrigir — some os dois motivos se ambos "
    "reprovaram.\n"
    "\n"
    'Rótulos do "veredito" no seu papel: "LIBERADO" ou "BLOQUEADO" (e NÃO '
    '"APROVADO"/"REPROVADO"). O motivo é vazio se LIBERADO; se BLOQUEADO, é a '
    "instrução de correção."
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
            system=_build_review_system(_APPROVER_ROLE),
            messages=[{"role": "user", "content": user}],
        )
        log_usage(TAG, "final_approval", resp.usage)
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
