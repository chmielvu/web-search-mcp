import json, urllib.request

queries = {
    "general": [
        "Why do cats purr scientific explanation",
        "How does photosynthesis work simple explanation",
    ],
    "ai_coding_and_infrastructure": [
        "Python asyncio gather vs create_task differences",
        "Docker compose multi-stage build best practices",
    ],
    "digital_humanities": [
        "Digital preservation of ancient manuscripts techniques",
        "Sentiment analysis of Victorian era literature",
    ],
    "comparison": [
        "PostgreSQL vs MySQL performance comparison 2026",
        "Kubernetes vs Docker Swarm for small teams",
    ],
    "social_media": [
        "Latest TikTok marketing trends 2026",
        "Instagram vs Facebook ads engagement comparison",
    ],
    "news": [
        "Breaking news API real-time integration",
        "Automated news article summarization tools",
    ],
}

correct = 0
total = 0
confusion = {}

for expected, qs in queries.items():
    print(f"\n=== {expected.upper()} ===")
    for q in qs:
        total += 1
        data = json.dumps({"text": q}).encode()
        req = urllib.request.Request(
            "http://localhost:8686/classify",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=300).read())
        predicted = resp["intent"]
        scores_str = " ".join(f'{s["label"][:8]:>8}={s["score"]:.3f}' for s in resp["scores"])
        match = "OK" if predicted == expected else "!!"
        if predicted == expected:
            correct += 1
        key = f"{expected} -> {predicted}"
        confusion[key] = confusion.get(key, 0) + 1
        print(f"  [{match}] {q}")
        print(f"         {scores_str}")

print(f"\n{'='*50}")
print(f"ACCURACY: {correct}/{total} = {correct/total*100:.1f}%")
print(f"\nCONFUSION MATRIX:")
for k, v in sorted(confusion.items()):
    print(f"  {k}: {v}")