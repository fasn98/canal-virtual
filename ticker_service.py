import time
import os
import redis

r = redis.Redis(host="redis", decode_responses=True)

while True:
    # Lê as últimas 5 notícias
    msgs = r.xrevrange("news.raw", count=5)

    if msgs:
        headlines = " • ".join([m[1].get("title", "") for m in msgs])
        text = "Últimas notícias: " + headlines

        # Atualiza o ticker.txt
        with open("/app/ticker/ticker.txt", "w") as f:
            f.write(text)

        # Regenera o ticker.png
        os.system("bash /app/generate_ticker.sh")

        print("[Ticker] Atualizado:", text)

    time.sleep(10)
