import redis
import time
from ultrafast_client import UltraFastClient

r = redis.Redis(host="redis", decode_responses=True)
client = UltraFastClient()

last_id = "0-0"

while True:
    msgs = r.xread({"news.categorized": last_id}, block=1000, count=1)

    if msgs:
        stream, entries = msgs[0]

        for entry_id, data in entries:
            last_id = entry_id

            improved = client.enhance_news(
                title=data["title"],
                summary=data["summary"],
                category=data["category"]
            )

            r.xadd("news.ultrafast", improved)
            print("Ultrafast → news.ultrafast:", improved)

    time.sleep(1)
