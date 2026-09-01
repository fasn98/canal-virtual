"""Ponte para a revisão editorial — SEM caminho paralelo.

O roteiro do resumo passa exatamente pelos mesmos 3 agentes que uma notícia
individual: `review_commentary()` de `commentator/review.py` (copiado para
`worldin3/review.py` no build). Aqui só montamos o material-fonte e o laço de
correção, no mesmo formato do `produce_approved_commentary` do commentator.

material-fonte entregue ao verificador de fatos = as manchetes originais +
a análise de cada uma que JÁ foi aprovada pelos 3 revisores quando a notícia
passou sozinha pelo pipeline. Ou seja: a base factual do resumo é, no mínimo,
tão auditada quanto a de uma notícia avulsa.
"""

from . import config, scriptgen
from .review import ReviewUnavailable, review_commentary  # cópia de commentator/review.py


def _source_material(headlines):
    partes = []
    for i, h in enumerate(headlines, 1):
        partes.append(
            f"[{i}] FONTE {h['source'] or '(agência)'} | CATEGORIA {h['category']}\n"
            f"TÍTULO ORIGINAL: {h['title_original']}\n"
            f"TÍTULO (pt): {h['title']}\n"
            f"ANÁLISE JÁ APROVADA PELA REDAÇÃO:\n{h['commentary'] or '(sem análise adicional)'}"
        )
    return "\n\n".join(partes)


def produce_approved_digest(client, headlines, target_words):
    """Gera o roteiro e o submete aos 3 revisores; em BLOQUEADO, devolve para
    reescrita com o motivo, até config.MAX_CORRECTION_ATTEMPTS.

    Retorna (narração_liberada, legendas, trilha) onde `trilha` é a lista de
    tentativas [{attempt, liberado, motivo}]. Retorna (None, [], trilha) se
    esgotou as correções (deve ser descartado, não publicado). Levanta
    ReviewUnavailable se os revisores não puderam ser consultados (API fora).

    As legendas do rodapé são geradas junto e passam pelos MESMOS revisores
    (anexadas ao texto revisado) — não escapam da checagem factual/editorial."""
    title = f"O Mundo em 3 Minutos — {config.period_label()}"
    material = _source_material(headlines)
    source = ", ".join(sorted({h["source"] for h in headlines if h["source"]})) or "BBC"

    trilha = []
    prev_text, motivo = None, None
    for tentativa in range(config.MAX_CORRECTION_ATTEMPTS + 1):
        correction = None if tentativa == 0 else (prev_text, motivo)
        narration, caps = scriptgen.generate_digest(
            client, headlines, target_words, correction
        )
        reviewed = narration + "\n\n[LEGENDAS DO RODAPÉ]\n" + "\n".join(caps)

        if not config.ENABLE_EDITORIAL_REVIEW:
            # Paridade com o commentator (ENABLE_EDITORIAL_REVIEW=false): sem
            # revisão. O preview avisa em alto e bom som que isso aconteceu.
            trilha.append({"attempt": tentativa, "liberado": True, "motivo": "(revisão desligada)"})
            return narration, caps, trilha

        liberado, motivo = review_commentary(
            client=client,
            news_id="worldin3-preview",
            commentary=reviewed,
            title=title,
            title_original=title,
            summary=material,
            category="Resumo",
            source=source,
        )
        trilha.append({"attempt": tentativa, "liberado": liberado, "motivo": motivo})
        if liberado:
            return narration, caps, trilha
        prev_text = reviewed

    return None, [], trilha
