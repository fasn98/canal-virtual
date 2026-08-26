import time
import redis
import feedparser
import hashlib
import json

RSS_FEED_URL = "https://feeds.bbci.co.uk/news/world/rss.xml"

r = redis.Redis(host="redis", port=6379, decode_responses=True)

def get_news():
    feed = feedparser.parse(RSS_FEED_URL)
    items = []
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        summary = entry.get("summary", "").strip()
        link = entry.get("link", "").strip()
        published = entry.get("published", "")

        if not title or not link:
            continue

        # ID estável baseado no link
        uid = hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]

        items.append({
            "id": uid,
            "title": title,
            "summary": summary,
            "link": link,
            "published": published,
        })
    return items

def main():
    seen_ids = set()

    while True:
        try:
            news_items = get_news()
            for item in news_items:
                if item["id"] in seen_ids:
                    continue

                msg = {
                    "id": item["id"],
                    "title": item["title"],
                    "summary": item["summary"],
                    "link": item["link"],
                    "published": item["published"],
                    "source": RSS_FEED_URL,
                    "timestamp": time.time(),
                }

                r.xadd("news.raw", msg)
                print("Collector → news.raw:", json.dumps(msg, ensure_ascii=False))

                seen_ids.add(item["id"])

            time.sleep(60)  # consulta a cada 1 minuto

        except Exception as e:
            print("Collector ERROR:", e)
            time.sleep(10)

if __name__ == "__main__":
    main()
