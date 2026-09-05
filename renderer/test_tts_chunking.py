"""Testes do chunking de TTS (lib/gpu_client._chunk_text_for_tts + _concat_wavs).
Sem pod nem rede — o _concat_wavs usa ffmpeg com WAVs sintéticos.

Uso (raiz do repo):  python3 -m renderer.test_tts_chunking
"""
import os
import re
import subprocess
import sys
import tempfile

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.gpu_client import _chunk_text_for_tts, _concat_wavs

LONG = (
    "O banco central manteve a taxa básica de juros inalterada nesta quinta-feira. "
    "A decisão reflete um equilíbrio delicado entre conter a inflação persistente e "
    "não sufocar a atividade econômica, que já dá sinais de desaceleração em setores "
    "sensíveis ao crédito. Segundo o comunicado, os diretores avaliaram que os riscos "
    "para os preços seguem elevados, embora o cenário externo tenha melhorado. "
    "Analistas agora divergem sobre o momento do primeiro corte. "
) * 3


def _wav(path, seconds, freq=220):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"sine=frequency={freq}:sample_rate=24000:duration={seconds}",
         "-ac", "1", "-c:a", "pcm_s16le", path],
        check=True,
    )


def _dur(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _sr_ch(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=sample_rate,channels", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()


def main():
    ok = True

    # 1) respeita o teto de chars
    for mc in (200, 350, 450):
        c = _chunk_text_for_tts(LONG, mc)
        bad = [len(x) for x in c if len(x) > mc]
        print(f"[{'OK ' if not bad else 'FAIL'}] max_chars={mc}: {len(c)} blocos, "
              f"maiores={sorted((len(x) for x in c), reverse=True)[:3]}")
        ok &= not bad

    # 2) nada de texto perdido (comparação por palavras)
    c = _chunk_text_for_tts(LONG, 350)
    w_in = re.findall(r"\w+", LONG)
    w_out = re.findall(r"\w+", " ".join(c))
    print(f"[{'OK ' if w_in == w_out else 'FAIL'}] palavras in={len(w_in)} out={len(w_out)}")
    ok &= w_in == w_out

    # 3) casos degenerados
    print(f"[{'OK ' if _chunk_text_for_tts('', 350) == [] else 'FAIL'}] texto vazio -> []")
    ok &= _chunk_text_for_tts("", 350) == []
    one = _chunk_text_for_tts("Frase curta.", 350)
    print(f"[{'OK ' if one == ['Frase curta.'] else 'FAIL'}] 1 frase curta -> 1 bloco")
    ok &= one == ["Frase curta."]
    # frase única gigante sem pontuação -> sub-quebra por espaço
    huge = "palavra " * 200
    hc = _chunk_text_for_tts(huge, 100)
    print(f"[{'OK ' if hc and all(len(x) <= 100 for x in hc) else 'FAIL'}] "
          f"frase gigante s/ pontuação -> {len(hc)} blocos <= 100 ch")
    ok &= bool(hc) and all(len(x) <= 100 for x in hc)

    # 4) _concat_wavs: 3 WAVs 24k mono -> 1 WAV, duração somada, 24k mono
    d = tempfile.mkdtemp(prefix="chunk_test_")
    ws = [os.path.join(d, f"{i}.wav") for i in range(3)]
    durs = [5.0, 3.0, 4.0]
    for w, s in zip(ws, durs):
        _wav(w, s)
    outp = os.path.join(d, "cat.wav")
    _concat_wavs(ws, outp)
    total = _dur(outp)
    srch = _sr_ch(outp)
    dur_ok = abs(total - sum(durs)) < 0.3
    fmt_ok = srch == "24000,1"
    print(f"[{'OK ' if dur_ok else 'FAIL'}] concat 3 WAVs: {total:.2f}s (esperado {sum(durs):.1f}s)")
    print(f"[{'OK ' if fmt_ok else 'FAIL'}] formato de saída: {srch} (esperado 24000,1)")
    ok &= dur_ok and fmt_ok

    # 5) _concat_wavs com 1 entrada só re-amostra pra 24k mono
    src48 = os.path.join(d, "src48.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=300:sample_rate=48000:duration=2", "-ac", "2",
                    "-c:a", "pcm_s16le", src48], check=True)
    out1 = os.path.join(d, "one.wav")
    _concat_wavs([src48], out1)
    one_ok = _sr_ch(out1) == "24000,1" and abs(_dur(out1) - 2.0) < 0.2
    print(f"[{'OK ' if one_ok else 'FAIL'}] 1 WAV 48k stereo -> {_sr_ch(out1)} {_dur(out1):.2f}s")
    ok &= one_ok

    print("\n" + ("TODOS OS CASOS OK" if ok else "HÁ FALHAS"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
