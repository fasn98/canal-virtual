"""Formato de Short "O Mundo em 3 Minutos".

Resumo condensado das principais manchetes reais do dia numa narração única
(~3 min, vertical 9:16), pensado para rodar 3x ao dia. Reaproveita ao máximo o
que já existe no pipeline do canal:

  - manchetes reais de `news.ready` (mesmo critério do ticker do renderer);
  - os MESMOS 3 revisores do commentator (verificador de fatos + revisor
    editorial + aprovador final) — ver `worldin3/review.py` (cópia em build do
    `commentator/review.py`, NÃO um caminho de revisão paralelo);
  - a mesma síntese ElevenLabs (mesma voz, mesmo modelo);
  - os mesmos assets de estúdio (fundo, apresentadora, lip-sync D-ID opcional
    com fallback estático) e a mesma trilha licenciada em loop.

PARTE 1 (este commit): geração + composição + PREVIEW LOCAL. Nada sobe para o
YouTube e nada é agendado. O entrypoint é `python -m worldin3.preview`.

PARTE 2 (depois da aprovação do preview): upload, agendamento nos horários e
distribuição via TubeOptimizer (`worldin3/lib/tubeoptimizer_client.py`).
"""
