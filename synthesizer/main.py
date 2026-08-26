import time
import redis
import json

r = redis.Redis(host="redis", port=6379, decode_responses=True, socket_timeout=10)

def safe(v):
    if v is None:
        return ""
    return str(v)

def main():
    last_id = "$"

    while True:
        try:
            msgs = r.xread({"news.final": last_id}, block=5000, count=10)

            if not msgs:
                continue

            for stream, events in msgs:
                for event_id, data in events:
                    last_id = event_id

                    out = {
                        "id": safe(data.get("id")),
                        "title": safe(data.get("title")),
                        "category": safe(data.get("category")),
                        "commentary": safe(data.get("commentary")),
                        "timestamp": time.time()
                    }

                    r.xadd("news.ready", out)
                    print("Synthesizer → news.ready:", json.dumps(out, ensure_ascii=False))

        except Exception as e:
            print("Synthesizer ERROR:", e)
            time.sleep(2)

if __name__ == "__main__":
    main()
