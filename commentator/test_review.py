"""Teste manual da etapa de revisão editorial (Parte B).

Roda DOIS casos contra a API real do Claude e imprime os logs de decisão:

  CASO 1 — comentário limpo, deve passar (LIBERADO).
  CASO 2 — comentário com estatística INVENTADA (não está no resumo da fonte):
           2a) o verificador de fatos reprova e, na reescrita, uma versão limpa
               é LIBERADA — mostra o fluxo "devolve pro commentator";
           2b) o gerador insiste no texto ruim -> após as tentativas de
               correção o item é DESCARTADO (não publicado).

Uso (dentro do container, que já tem anthropic + ANTHROPIC_API_KEY):

    docker compose exec commentator python test_review.py
"""

import main
import review

# --- Fonte fictícia porém plausível (o material "original" da agência) --------
TITLE_ORIGINAL = "Central bank holds interest rates steady amid inflation concerns"
TITLE_PT = "Banco central mantém juros estáveis em meio a preocupações com a inflação"
SUMMARY = (
    "The central bank kept its benchmark interest rate unchanged on Thursday, "
    "citing persistent inflation and global economic uncertainty. Policymakers "
    "said they would wait for more data before deciding on any future move."
)
CATEGORY = "Economy"
SOURCE = "BBC"

# Comentário LIMPO: só análise/contexto, sem dado específico fora da fonte.
CLEAN_COMMENTARY = (
    "A decisão do banco central de manter a taxa de juros inalterada sinaliza "
    "uma postura de cautela diante de um quadro ainda incerto. Ao optar por não "
    "mexer nos juros, a autoridade monetária evita tanto o risco de reacender "
    "pressões inflacionárias quanto o de sufocar a atividade econômica de forma "
    "abrupta. A menção à inflação persistente e à incerteza global indica que os "
    "dirigentes preferem acumular mais evidências antes de traçar um rumo. Para "
    "empresas e famílias, o recado é de previsibilidade no curto prazo, com o "
    "custo do crédito estável, mas sem garantia de alívio adiante. Os próximos "
    "indicadores de preços e de emprego devem ser determinantes para o momento "
    "em que o ciclo volte a se mover, em qualquer direção."
)

# Comentário com FABRICAÇÃO: números, votação e pesquisa que NÃO estão na fonte.
FABRICATED_COMMENTARY = (
    "A decisão do banco central de manter os juros foi tomada por 7 votos a 2, "
    "num colegiado dividido sobre o melhor caminho. Com a inflação anual rodando "
    "a 5,4% e as projeções apontando 4,1% para o fim do ano, o comitê preferiu "
    "esperar. Segundo levantamento recente, 62% dos economistas de mercado "
    "apostavam em corte nesta reunião, o que tornou a decisão uma surpresa. O "
    "presidente da instituição afirmou em entrevista que 'não há pressa para "
    "cortar', reforçando o tom conservador. O dólar fechou em queda de 1,2% "
    "após o anuncio."
)


def sep(txt):
    print("\n" + "=" * 78 + f"\n{txt}\n" + "=" * 78, flush=True)


def run():
    client = main.get_anthropic_client()
    resultados = {}

    # ---------------------------------------------------------------- CASO 1 --
    sep("CASO 1 — comentário limpo (esperado: LIBERADO)")
    liberado, motivo = review.review_commentary(
        client=client, news_id="TESTE-LIMPO",
        commentary=CLEAN_COMMENTARY, title=TITLE_PT, title_original=TITLE_ORIGINAL,
        summary=SUMMARY, category=CATEGORY, source=SOURCE,
    )
    resultados["caso1_liberado"] = liberado
    print(f"\n>>> resultado CASO 1: liberado={liberado} motivo={motivo!r}", flush=True)

    # ------------------------------------------------------------- CASO 2 (raw) --
    sep("CASO 2 — verificador de fatos sozinho no comentário com estatística inventada "
        "(esperado: REPROVADO)")
    fact_ok, fact_reason = review.fact_check(
        client, FABRICATED_COMMENTARY, TITLE_ORIGINAL, SUMMARY, SOURCE
    )
    resultados["caso2_fact_reprovado"] = not fact_ok
    print(f"\n>>> verificador de fatos: aprovado={fact_ok} motivo={fact_reason!r}", flush=True)

    # ------------------------------------------------------------- CASO 2a --
    sep("CASO 2a — fluxo completo: 1ª geração ruim -> reprovada -> reescrita limpa -> LIBERADO")
    _seq_2a = [
        (FABRICATED_COMMENTARY, True),   # tentativa 0 (inicial): ruim
        (CLEAN_COMMENTARY, True),        # tentativa 1 (correção): limpa
    ]

    def fake_gen_2a(title, summary, category, correction=None):
        return _seq_2a.pop(0)

    _real_gen = main.generate_commentary
    main.generate_commentary = fake_gen_2a
    try:
        aprovado = main.produce_approved_commentary(
            "TESTE-2A", TITLE_PT, TITLE_ORIGINAL, SUMMARY, CATEGORY, SOURCE
        )
    finally:
        main.generate_commentary = _real_gen
    resultados["caso2a_recuperado"] = aprovado is not None
    print(f"\n>>> resultado CASO 2a: {'LIBERADO após correção' if aprovado else 'descartado'}",
          flush=True)

    # ------------------------------------------------------------- CASO 2b --
    sep(f"CASO 2b — gerador insiste no texto ruim -> DESCARTA após "
        f"{review.MAX_CORRECTION_ATTEMPTS} correções")

    def fake_gen_2b(title, summary, category, correction=None):
        return (FABRICATED_COMMENTARY, True)

    main.generate_commentary = fake_gen_2b
    try:
        aprovado = main.produce_approved_commentary(
            "TESTE-2B", TITLE_PT, TITLE_ORIGINAL, SUMMARY, CATEGORY, SOURCE
        )
    finally:
        main.generate_commentary = _real_gen
    resultados["caso2b_descartado"] = aprovado is None
    print(f"\n>>> resultado CASO 2b: {'DESCARTADO (correto)' if aprovado is None else 'PUBLICOU (ERRADO)'}",
          flush=True)

    # ---------------------------------------------------------------- resumo --
    sep("RESUMO")
    checks = [
        ("CASO 1 comentário limpo -> LIBERADO", resultados["caso1_liberado"] is True),
        ("CASO 2 verificador pega estatística inventada -> REPROVADO",
         resultados["caso2_fact_reprovado"] is True),
        ("CASO 2a reescrita limpa -> LIBERADO", resultados["caso2a_recuperado"] is True),
        ("CASO 2b texto ruim insistente -> DESCARTADO", resultados["caso2b_descartado"] is True),
    ]
    ok = True
    for nome, passou in checks:
        print(f"  [{'PASS' if passou else 'FALHA'}] {nome}", flush=True)
        ok = ok and passou
    print(f"\n{'TODOS OS CHECKS PASSARAM' if ok else 'HOUVE FALHA — ver acima'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
