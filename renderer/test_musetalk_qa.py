"""Teste do gate de QA de áudio do provedor MuseTalk (renderer/musetalk.py).

Regressão do incidente de 04/09: o pod devolvia um MP4 com TTS embolada/curta
(~20-36 s no lugar de ~150 s) e ele ia direto ao ar, sem nenhuma checagem.
_validate_presenter_media() deve REPROVAR esse caso e liberar um áudio de
duração coerente com o texto.

Não precisa de pod nem de rede: constrói os fixtures com ffmpeg na hora.

Uso (na raiz do repo):
    python3 -m renderer.test_musetalk_qa
"""

import importlib
import os
import subprocess
import sys
import tempfile

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from renderer.musetalk import _validate_presenter_media

# Texto "de notícia": ~1800 caracteres => ~120 s esperados a 15 chars/s.
LONG_TEXT = (
    "Um julgamento criminal já é um processo em que as tensões costumam ser "
    "naturalmente altas, e essa situação ilustra bem por que a seleção e a "
    "manutenção de um corpo de jurados é tão delicada. Quando um dos jurados "
    "não concorda com a maioria, a dinâmica da deliberação muda completamente. "
) * 5


def _ffmpeg(*args):
    cmd = ["ffmpeg", "-y", "-v", "error", *args]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou: {res.stderr[-800:]}")


def _make(path, *, dur, audio="tone", vdur=None):
    """MP4 de teste: vídeo de cor + faixa de áudio.
      audio="tone"    -> senóide audível
      audio="silence" -> silêncio
      audio=None      -> sem faixa de áudio
    vdur: duração do vídeo (default = dur), para simular defasagem A/V.
    """
    vdur = dur if vdur is None else vdur
    if audio is None:
        _ffmpeg("-f", "lavfi", "-i", f"color=c=gray:s=320x240:r=25:d={vdur}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path)
        return
    asrc = "sine=frequency=220" if audio == "tone" else "anullsrc=r=24000:cl=mono"
    _ffmpeg("-f", "lavfi", "-i", f"color=c=gray:s=320x240:r=25:d={vdur}",
            "-f", "lavfi", "-i", f"{asrc}:d={dur}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-t", str(max(dur, vdur)), path)


def _expect_reject(name, path, text, needle):
    try:
        _validate_presenter_media(path, text)
    except RuntimeError as e:
        if needle.lower() in str(e).lower():
            print(f"[OK ] {name}: reprovado — {e}")
            return True
        print(f"[FAIL] {name}: reprovado com motivo inesperado — {e}")
        return False
    print(f"[FAIL] {name}: PASSOU (deveria reprovar)")
    return False


def _expect_pass(name, path, text):
    try:
        _validate_presenter_media(path, text)
        print(f"[OK ] {name}: liberado")
        return True
    except RuntimeError as e:
        print(f"[FAIL] {name}: reprovado (deveria passar) — {e}")
        return False


def main():
    tmp = tempfile.mkdtemp(prefix="musetalk_qa_")
    ok = True

    # 1. o incidente: áudio curtíssimo para texto longo -> razão << 0.65
    p = os.path.join(tmp, "short.mp4"); _make(p, dur=18)
    ok &= _expect_reject("áudio curto p/ texto longo", p, LONG_TEXT, "razão")

    # 2. sem faixa de áudio
    p = os.path.join(tmp, "noaudio.mp4"); _make(p, dur=120, audio=None)
    ok &= _expect_reject("sem stream de áudio", p, LONG_TEXT, "sem stream de áudio")

    # 3. áudio praticamente mudo
    p = os.path.join(tmp, "silent.mp4"); _make(p, dur=120, audio="silence")
    ok &= _expect_reject("áudio mudo", p, LONG_TEXT, "mudo")

    # 4. defasagem vídeo x áudio
    p = os.path.join(tmp, "skew.mp4"); _make(p, dur=120, vdur=90)
    ok &= _expect_reject("defasagem A/V", p, LONG_TEXT, "defasagem")

    # 5. piso absoluto de duração
    p = os.path.join(tmp, "tiny.mp4"); _make(p, dur=2)
    ok &= _expect_reject("abaixo do piso", p, "", "piso")

    # 6. coerente -> passa
    p = os.path.join(tmp, "good.mp4"); _make(p, dur=118)
    ok &= _expect_pass("áudio coerente com o texto", p, LONG_TEXT)

    # 7. QA desligado -> passa mesmo o caso 1
    os.environ["MUSETALK_QA_ENABLED"] = "false"
    import renderer.musetalk as mt
    importlib.reload(mt)
    p = os.path.join(tmp, "short2.mp4"); _make(p, dur=18)
    try:
        mt._validate_presenter_media(p, LONG_TEXT)
        print("[OK ] MUSETALK_QA_ENABLED=false: pulou a validação")
    except RuntimeError as e:
        print(f"[FAIL] MUSETALK_QA_ENABLED=false ainda reprovou — {e}")
        ok = False
    finally:
        os.environ.pop("MUSETALK_QA_ENABLED", None)

    print("\n" + ("TODOS OS CASOS OK" if ok else "HÁ FALHAS"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
