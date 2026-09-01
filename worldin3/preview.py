"""PARTE 1 — preview local do Short "O Mundo em 3 Minutos".

Roda o processo uma vez, à mão, e entrega:
  - a duração final exata do vídeo;
  - um frame da composição vertical (PNG);
  - o roteiro completo gerado (TXT);
  - a confirmação de que passou pelos 3 revisores.

NÃO sobe nada para o YouTube e NÃO agenda nada.

Uso (dentro do container):
    python -m worldin3.preview [--headlines N] [--budget] [--no-lipsync] [--lipsync]

Por padrão o preview IGNORA o freio de orçamento da ElevenLabs (é uma rodada
manual). Passe --budget para respeitá-lo.
"""

import argparse
import datetime
import os
import sys
import textwrap

from . import compose, config, headlines as hl, reviewbridge, scheduler
from .review import ReviewUnavailable


def _sep(t):
    print("\n" + "=" * 72 + f"\n {t}\n" + "=" * 72, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--headlines", type=int, default=config.HEADLINE_COUNT)
    ap.add_argument("--budget", action="store_true",
                    help="respeita o freio de orçamento da ElevenLabs")
    ap.add_argument("--lipsync", dest="lipsync", action="store_true", default=None)
    ap.add_argument("--no-lipsync", dest="lipsync", action="store_false")
    ap.add_argument("--voice-file", default=None,
                    help="usa este mp3 como voz em vez de chamar a ElevenLabs "
                         "(preview só-de-composição, p.ex. com a cota esgotada)")
    args = ap.parse_args(argv)

    if args.lipsync is not None:
        os.environ["ENABLE_LIPSYNC"] = "true" if args.lipsync else "false"
        config.ENABLE_LIPSYNC = args.lipsync

    stamp = datetime.datetime.now(config.tz()).strftime("%Y%m%d-%H%M")
    news_id = f"worldin3-{stamp}"
    out_dir = config.OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    mp4_path = os.path.join(out_dir, "worldin3_preview.mp4")
    frame_path = os.path.join(out_dir, "worldin3_preview_frame.png")
    script_path = os.path.join(out_dir, "worldin3_preview_script.txt")
    voice_path = os.path.join(config.ASSETS_DIR, "worldin3", "audio", f"{stamp}.mp3")

    _sep("AGENDA (confirmação de fuso — nada é disparado)")
    print(scheduler.describe(), flush=True)

    _sep(f"1) MANCHETES REAIS de {config.NEWS_READY_STREAM} (padrão do ticker)")
    items = hl.fetch_headlines(args.headlines)
    for i, h in enumerate(items, 1):
        print(f"  {i}. [{h['category']}/{h['source'] or '?'}] {h['title']}", flush=True)

    _sep("2) ROTEIRO + 3) REVISÃO PELOS 3 AGENTES (verificador / editorial / aprovador)")
    if not config.ENABLE_EDITORIAL_REVIEW:
        print("  ⚠️  ENABLE_EDITORIAL_REVIEW=false — revisão DESLIGADA. "
              "Ligue para produção.", flush=True)
    client = config.anthropic_client()

    target = config.TARGET_WORDS
    script = None
    caps = []
    trilha = []
    for shorten in range(config.SHORTEN_ATTEMPTS + 1):
        try:
            script, caps, trilha = reviewbridge.produce_approved_digest(client, items, target)
        except ReviewUnavailable as e:
            print(f"\n  ✖  Revisão indisponível (API): {e}\n"
                  "     Nada foi gerado. Tente de novo mais tarde.", flush=True)
            return 2
        if script is None:
            last = trilha[-1]["motivo"] if trilha else "(sem motivo)"
            _sep("RESULTADO: ROTEIRO REPROVADO PELA REVISÃO")
            print(f"  Tentativas de correção esgotadas ({config.MAX_CORRECTION_ATTEMPTS}).")
            print(f"  Último motivo do aprovador final: {last}")
            print("  Nada seria publicado (mesmo comportamento do commentator).")
            return 3

        # Estima a duração pela contagem de palavras antes de gastar TTS.
        wc = len(script.split())
        est = wc / 2.6  # ~156 palavras/min em PT-BR
        if est <= config.MAX_SECONDS or shorten == config.SHORTEN_ATTEMPTS:
            break
        new_target = int(target * (config.MAX_SECONDS / est) * 0.92)
        print(f"\n  Roteiro com {wc} palavras (~{est:.0f}s) pode passar de "
              f"{config.MAX_SECONDS:.0f}s. Regenerando com alvo {new_target} "
              f"palavras…", flush=True)
        target = new_target

    print(f"\n  ✔  Roteiro LIBERADO pelos 3 revisores "
          f"(tentativa {trilha[-1]['attempt']}; "
          f"{len(script.split())} palavras).", flush=True)
    for t in trilha:
        v = "LIBERADO" if t["liberado"] else f"BLOQUEADO — {t['motivo']}"
        print(f"     tentativa {t['attempt']}: {v}", flush=True)
    print("  legendas do rodapé (PT, revisadas junto):", flush=True)
    for i, c in enumerate(caps, 1):
        print(f"     {i}. {c}", flush=True)

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script + "\n\n===LEGENDAS DO RODAPÉ (PT)===\n")
        for i, c in enumerate(caps, 1):
            f.write(f"{i}. {c}\n")

    cta_hit = any(
        k in script.lower()
        for k in ("ao vivo", "24 horas", "vinte e quatro horas", "transmissão")
    )
    if not cta_hit:
        print("  ⚠️  o encerramento não parece conter o convite à live 24h — revise o roteiro.",
              flush=True)

    _sep("4) SÍNTESE DE VOZ (ElevenLabs — mesma voz/modelo do synthesizer)")
    placeholder_voice = False
    from . import tts

    if args.voice_file:
        placeholder_voice = True
        voice_path = args.voice_file
        print(f"  ⚠️  --voice-file: usando {voice_path} como VOZ DE PLACEHOLDER "
              "(ElevenLabs não foi chamada). Composição/duração são reais; o "
              "áudio NÃO é a narração deste roteiro.", flush=True)
    else:
        try:
            tts.synthesize(script, voice_path, ignore_budget=not args.budget)
        except RuntimeError as e:
            print(f"  ✖  {e}", flush=True)
            return 4
    voice_dur = compose.probe_duration(voice_path)
    render_seconds = min(voice_dur, config.MAX_SECONDS)
    print(f"  áudio: {voice_dur:.2f}s  →  vídeo será cortado em "
          f"{render_seconds:.2f}s (teto {config.MAX_SECONDS:.0f}s)", flush=True)
    trimmed = voice_dur > config.MAX_SECONDS + 0.05

    _sep("5) COMPOSIÇÃO VERTICAL 9:16")
    _mp4, used_lipsync = compose.compose_vertical(
        voice_path, caps, mp4_path, render_seconds, news_id
    )
    final_dur = compose.probe_duration(mp4_path)
    compose.extract_frame(mp4_path, frame_path, at_seconds=min(6.0, final_dur / 2))

    _sep("PREVIEW PRONTO — PARTE 1 (nada foi ao ar / nada foi agendado)")
    size_mb = os.path.getsize(mp4_path) / 1e6
    print(textwrap.dedent(f"""\
        vídeo .............. {mp4_path}  ({size_mb:.1f} MB, {config.OUT_W}x{config.OUT_H})
        duração final ...... {final_dur:.2f} s   ({int(final_dur // 60)}:{final_dur % 60:05.2f})
        teto configurado ... {config.MAX_SECONDS:.0f} s (WORLDIN3_MAX_SECONDS)
        {"⚠️  áudio passou do teto e foi cortado com fade — considere baixar WORLDIN3_TARGET_WORDS" if trimmed else "dentro do teto, com margem"}
        frame ............. {frame_path}
        roteiro ........... {script_path}
        lip-sync D-ID ..... {"SIM (D-ID)" if used_lipsync else "não (avatar estático)"}
        revisão 3 agentes . PASSOU ({len(trilha)} tentativa(s); ver verdicts acima)
        manchetes ......... {len(items)}  de {config.NEWS_READY_STREAM}
    """), flush=True)
    if placeholder_voice:
        print("⚠️  ÁUDIO É PLACEHOLDER (cota ElevenLabs esgotada). Aprovar a "
              "COMPOSIÇÃO/ENQUADRAMENTO e o ROTEIRO agora; regenerar o áudio real "
              "e reconferir a duração quando a cota voltar.", flush=True)
    print("Roteiro gerado:\n" + "-" * 72)
    print(script)
    print("-" * 72, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
