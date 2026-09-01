"""Geração do roteiro do resumo "O Mundo em 3 Minutos" via Claude.

Um único texto corrido em PT-BR, ~TARGET_WORDS palavras, cobrindo rapidamente
cada manchete, com abertura breve e encerramento que convida para a live 24h
(config.CLOSING_CTA — mesmo tom da chamada promocional do promoter).

Este módulo SÓ gera o rascunho. A revisão obrigatória pelos 3 agentes
(verificador de fatos / revisor editorial / aprovador final) acontece em
`worldin3/reviewbridge.py`, reaproveitando `review_commentary` do commentator.
"""

from . import config

_SYSTEM = (
    "Você é o roteirista-chefe de um telejornal. Escreve o texto que a "
    "apresentadora vai narrar, em português do Brasil, para um resumo diário "
    "condensado das principais notícias — o quadro 'O Mundo em 3 Minutos'."
)


def _headlines_block(headlines):
    linhas = []
    for i, h in enumerate(headlines, 1):
        ctx = h["commentary"]
        if len(ctx) > 900:
            ctx = ctx[:900].rsplit(" ", 1)[0] + "…"
        linhas.append(
            f"{i}. [{h['category']} — fonte {h['source'] or 'agência'}] {h['title']}\n"
            f"   Contexto já apurado e aprovado pela redação: {ctx or '(sem contexto adicional)'}"
        )
    return "\n\n".join(linhas)


def build_prompt(headlines, target_words, correction=None):
    n = len(headlines)
    base = (
        f"Escreva o roteiro de narração do resumo 'O Mundo em 3 Minutos' "
        f"({config.period_label()}), cobrindo as {n} manchetes abaixo.\n\n"
        f"=== MANCHETES E CONTEXTO (única base factual permitida) ===\n"
        f"{_headlines_block(headlines)}\n\n"
        "=== REGRAS ===\n"
        f"- Alvo de {target_words} palavras (tolerância de ~8%). É melhor ficar "
        "um pouco ABAIXO do que acima.\n"
        "- Abertura de 1 a 2 frases apresentando o quadro e o momento do dia.\n"
        f"- Em seguida, cobertura de TODAS as {n} manchetes, na ordem dada, "
        "2 a 3 frases cada, com uma transição curta entre elas. Diga o essencial: "
        "o que aconteceu e por que importa. Seja enxuto para sobrar espaço para o "
        "encerramento abaixo.\n"
        "- NÃO invente fatos, números, nomes, datas, valores ou declarações que "
        "não estejam nas manchetes e no contexto acima. Sem contexto suficiente, "
        "fale de forma geral, sem especular dados.\n"
        "- Tom de telejornal sério: sem sensacionalismo, sem opinião apresentada "
        "como fato, sem adjetivar de forma desigual os envolvidos.\n"
        "- ENCERRAMENTO OBRIGATÓRIO (não pule, não resuma, não substitua por "
        "'foi o Mundo em Três Minutos'): as 2 ou 3 ÚLTIMAS frases do roteiro "
        "têm de ser o convite abaixo para a transmissão ao vivo 24h. Pode "
        "adaptar levemente a redação, mas mantenha o sentido e o tom:\n"
        f"  \"{config.CLOSING_CTA}\"\n"
        "- Texto corrido para locução: só o que a apresentadora fala. Sem título, "
        "sem marcadores, sem markdown, sem rubricas de cena, sem '[música]'. "
        "Comece direto na fala.\n\n"
        "=== FORMATO DA RESPOSTA ===\n"
        "Primeiro o roteiro de narração (texto corrido). Depois, numa linha "
        "isolada, exatamente: ===LEGENDAS===\n"
        f"Depois {n} linhas, uma por manchete NA MESMA ORDEM, cada uma com uma "
        "legenda curta em português (máx. 60 caracteres, sem aspas, sem "
        "numeração) que resuma a manchete para quem assiste sem som.\n"
    )
    if correction:
        prev, motivo = correction
        base += (
            "\n=== ATENÇÃO — REESCRITA ===\n"
            "A versão anterior foi REPROVADA na revisão editorial pelo motivo:\n"
            f"    {motivo}\n"
            "Reescreva o roteiro inteiro corrigindo especificamente esse problema, "
            "sem sair da base factual acima. NÃO repita os erros da versão "
            f"anterior:\n---\n{prev}\n---\n"
        )
    return base


_CAP_MARK = "===LEGENDAS==="


def _split_caps(raw, headlines):
    """Separa (narração, legendas[]). Se o modelo não devolver o bloco de
    legendas, cai para o próprio título de cada manchete (melhor que nada)."""
    if _CAP_MARK in raw:
        narr, _, tail = raw.partition(_CAP_MARK)
        caps = [
            ln.strip(" -•\t").strip()
            for ln in tail.splitlines()
            if ln.strip(" -•\t").strip()
        ]
    else:
        narr, caps = raw, []
    narr = narr.strip()
    caps = [c[:70] for c in caps][: len(headlines)]
    while len(caps) < len(headlines):
        caps.append(headlines[len(caps)]["title"])
    return narr, caps


def generate_digest(client, headlines, target_words, correction=None):
    """Chama o Claude e devolve (narração:str, legendas:list[str]). Levanta em
    erro de API — o preview trata (sem fallback silencioso: um resumo sem
    revisão não pode ir ao ar)."""
    prompt = build_prompt(headlines, target_words, correction)
    max_toks = min(4000, max(1200, int(target_words * 3.5)))
    resp = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=max_toks,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "\n".join(
        b.text.strip()
        for b in resp.content
        if getattr(b, "type", None) == "text" and b.text.strip()
    ).strip()
    if not text:
        raise RuntimeError("resposta vazia da API ao gerar o roteiro")
    return _split_caps(text, headlines)


def word_count(text):
    return len(text.split())
