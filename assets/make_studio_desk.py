"""Gera assets/studio_desk.png — uma bancada/console curvo em PNG RGBA de canvas
inteiro (1920x1080), pra compositar em PRIMEIRO PLANO na frente da parte
inferior do apresentador (D-ID e MuseTalk), ancorando a figura na cena
`studio_bg_novo.png` (que não tem mesa). Camada: fundo -> apresentador ->
ESTA -> logo -> lower third -> ticker.

A borda superior (rim) é um arco de elipse visto de frente: ponto MAIS BAIXO
no centro (borda da mesa perto da câmera) subindo suavemente nas laterais.

Uso:  python3 assets/make_studio_desk.py [center_y] [rim_rise]
  center_y  y do ponto mais baixo do rim, no centro do frame  (default 660)
  rim_rise  quanto o rim sobe (px) do centro até a lateral      (default 46)
Saída: assets/studio_desk.png  e  volumes/assets/studio_desk.png
"""
import os
import sys

from PIL import Image

W, H = 1920, 1080
CENTER_Y = int(sys.argv[1]) if len(sys.argv) > 1 else 685
RIM_RISE = int(sys.argv[2]) if len(sys.argv) > 2 else 46

# paleta (casa com o azul-marinho + cyan do estúdio) — desk claramente iluminada
GLASS_TOP = (54, 118, 176)    # topo do tampo de vidro (logo abaixo do rim)
FASCIA_TOP = (30, 74, 124)    # topo da saia frontal
FASCIA_BOT = (6, 14, 30)      # base da saia frontal
PANEL_A = 255                 # opacidade base do painel
RIM = (198, 240, 255)         # núcleo da linha de luz do rim
GLOW = (70, 180, 246)         # halo do rim
ACCENT = (90, 190, 240)       # 2a linha fina de accent

GLASS_BAND = 46               # espessura do "tampo" translúcido sob o rim


def rim_y(x: int) -> float:
    t = (x - W / 2) / (W / 2)
    return CENTER_Y - RIM_RISE * (t * t)


img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
px = img.load()

for x in range(W):
    ry = rim_y(x)
    for y in range(int(ry) - 20, H):
        d = y - ry
        if d < -20:
            continue
        if d < 0:
            # halo suave acima do rim
            a = max(0, min(210, int(210 * (1 + d / 20))))
            px[x, y] = (*GLOW, a)
        elif d < 6:
            px[x, y] = (*RIM, 255)                       # núcleo da luz
        elif d < 12:
            px[x, y] = (*RIM, 255)                       # brilho fino
        elif d < GLASS_BAND:
            # tampo de vidro translúcido pegando luz
            f = (d - 12) / (GLASS_BAND - 12)
            r = int(GLASS_TOP[0] + (FASCIA_TOP[0] - GLASS_TOP[0]) * f)
            g = int(GLASS_TOP[1] + (FASCIA_TOP[1] - GLASS_TOP[1]) * f)
            b = int(GLASS_TOP[2] + (FASCIA_TOP[2] - GLASS_TOP[2]) * f)
            px[x, y] = (r, g, b, 255)
        else:
            # saia frontal — gradiente vertical
            f = min(1.0, (y - ry - GLASS_BAND) / (H - ry - GLASS_BAND))
            r = int(FASCIA_TOP[0] + (FASCIA_BOT[0] - FASCIA_TOP[0]) * f)
            g = int(FASCIA_TOP[1] + (FASCIA_BOT[1] - FASCIA_TOP[1]) * f)
            b = int(FASCIA_TOP[2] + (FASCIA_BOT[2] - FASCIA_TOP[2]) * f)
            px[x, y] = (r, g, b, PANEL_A)

# 2a linha fina de accent ~16px abaixo do rim, dá relevo ao tampo
for x in range(W):
    ry = int(rim_y(x))
    for y in range(ry + 14, ry + 17):
        if 0 <= y < H:
            b = px[x, y]
            px[x, y] = (min(255, b[0] + ACCENT[0] // 3),
                        min(255, b[1] + ACCENT[1] // 3),
                        min(255, b[2] + ACCENT[2] // 3), b[3])

# faixas verticais sutis de luz na saia (painéis do console)
for cx in (W // 2, W // 2 - 430, W // 2 + 430, W // 2 - 820, W // 2 + 820):
    for x in range(cx - 2, cx + 3):
        if not (0 <= x < W):
            continue
        ry = int(rim_y(x))
        for y in range(ry + GLASS_BAND, H):
            b = px[x, y]
            if b[3]:
                px[x, y] = (min(255, b[0] + 14), min(255, b[1] + 26),
                            min(255, b[2] + 40), b[3])

# leve vinheta lateral pra não competir com as bordas do frame
for x in list(range(0, 140)) + list(range(W - 140, W)):
    fade = (min(x, W - 1 - x)) / 140
    for y in range(H):
        b = px[x, y]
        if b[3]:
            px[x, y] = (b[0], b[1], b[2], int(b[3] * (0.30 + 0.70 * fade)))

for out in ("/opt/canal-virtual/assets/studio_desk.png",
            "/opt/canal-virtual/volumes/assets/studio_desk.png"):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out)
    print("salvo:", out, os.path.getsize(out), "B")
