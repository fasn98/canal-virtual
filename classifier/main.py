import time
import redis
import json

r = redis.Redis(host="redis", port=6379, decode_responses=True, socket_timeout=10)

CATEGORIES = {
    "Politics": ["election", "president", "government", "minister", "parliament", "policy", "macron", "biden", "trump"],
    "Economy": ["inflation", "market", "economy", "trade", "finance", "stocks", "bank"],
    "Technology": ["ai", "tech", "software", "hardware", "robot", "cyber", "data"],
    "Health": ["health", "virus", "covid", "hospital", "disease", "medical"],
    "Science": ["research", "scientist", "study", "space", "nasa"],
    "Climate": ["climate", "heatwave", "wildfire", "flood", "drought", "environment"],
    "Security": ["shooting", "attack", "police", "crime", "military", "strike"],
    "Entertainment": ["film", "movie", "actor", "actress", "music", "netflix"],
    "Sports": ["football", "soccer", "nba", "fifa", "olympics"],
    "Lifestyle": ["travel", "fashion", "culture", "food"]
}

def classify(text):
    text_lower = text.lower()
    scores = {}

    for category, keywords in CATEGORIES.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[category] = score

    if not scores:
        return "World", 0.1

    best_category = max(scores, key=scores.get)
    confidence = scores[best_category] / (len(CATEGORIES[best_category]) + 1)

    return best_category, round(confidence, 2)

def main():
    last_id = "$"

    while True:
        try:
            msgs = r.xread({"news.raw": last_id}, block=5000, count=10)

            if not msgs:
                continue

            for stream, events in msgs:
                for event_id, data in events:
                    last_id = event_id

                    title = data.get("title", "")
                    summary = data.get("summary", "")
                    text = f"{title} {summary}".strip()

                    category, confidence = classify(text)

                    msg = {
                        "id": data.get("id", ""),
                        "title": title,
                        "summary": summary,
                        "category": category,
                        "confidence": confidence,
                        "link": data.get("link", ""),
                        "published": data.get("published", ""),
                        "source": data.get("source", ""),
                        "timestamp": time.time(),
                    }

                    r.xadd("news.classified", msg)
                    print("Classifier → news.classified:", json.dumps(msg, ensure_ascii=False))

        except Exception as e:
            print("Classifier ERROR:", e)
            time.sleep(5)

if __name__ == "__main__":
    main()
