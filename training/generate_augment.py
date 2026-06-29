#!/usr/bin/env python3
"""Generate additional synthetic data for weak classes (general, ai_coding_and_infrastructure)."""

import json, os, warnings
from pathlib import Path
from collections import Counter
warnings.filterwarnings("ignore")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash-lite"
os.environ.pop("GOOGLE_API_KEY", None)

from google import genai
from google.genai import types as t

client = genai.Client(api_key=GEMINI_API_KEY)

LABELS_AND_TOPICS = {
    "general": [
        "science discoveries and how-things-work explanations",
        "history, geography, and world culture facts",
        "health, nutrition, and general wellness advice",
        "travel destinations, tips, and cultural guides",
        "sports rules, athlete biographies, and game strategies",
        "music theory, artist discographies, and instrument tutorials",
        "movie plots, TV show trivia, and entertainment news",
        "philosophy, ethics, and critical thinking topics",
        "education resources, study tips, and academic writing",
        "cooking recipes, gardening, and DIY home projects",
        "financial planning, personal budgeting, and investing basics",
        "car maintenance, driving tips, and vehicle comparisons",
        "pet care, animal behavior, and wildlife facts",
        "fashion trends, style guides, and clothing care",
        "weather phenomena, climate science, and natural disasters",
        "parenting advice, family activities, and child development",
        "career guidance, resume tips, and job interview prep",
        "language learning, translation help, and grammar questions",
        "legal basics, consumer rights, and insurance information",
        "product reviews, buying guides, and price comparisons",
    ],
    "ai_coding_and_infrastructure": [
        "Python library usage and API documentation",
        "Docker compose configuration and multi-service orchestration",
        "Kubernetes cluster setup, Helm charts, and pod networking",
        "CI/CD pipeline configuration with GitHub Actions or GitLab",
        "AWS, GCP, or Azure service specific setup guides",
        "Linux server administration, bash scripting, and automation",
        "TypeScript type definitions and React component patterns",
        "PostgreSQL query optimization and indexing strategies",
        "REST API design, OpenAPI specs, and endpoint versioning",
        "Redis caching patterns, cluster setup, and eviction policies",
        "Terraform modules, Pulumi stacks, and infrastructure-as-code",
        "Nginx reverse proxy configuration and SSL cert management",
        "Prometheus metrics, Grafana dashboards, and alerting rules",
        "Git branching strategies, merge workflows, and rebase best practices",
        "ElasticSearch indexing, query DSL, and cluster management",
        "WebSocket server implementation and real-time communication",
        "gRPC service definition, protocol buffers, and streaming",
        "Message queue setup with RabbitMQ or Kafka topics",
        "SSH key management, VPN configuration, and network security",
        "Monitoring with Datadog, New Relic, or OpenTelemetry traces",
    ],
}

PROMPT = """Generate 5 realistic web search queries that a software developer, researcher, or curious person would type into a search engine.

Topic area: {topic}
Category: {label}

Rules:
- Queries must sound like real human search queries
- Vary in length (5-25 words)
- Mix of questions, keyword searches, and natural language
- Do NOT include the label name in the query
- Do NOT number or prefix the queries
- Each query on its own line, no extra text

Output ONLY the 5 queries, one per line:"""

output_path = Path("training/data/synthetic_augment.jsonl")
records = []
SEEN_QUERIES = set()

# Load existing queries to avoid duplication
for f in ["training/data/train.jsonl", "training/data/val.jsonl", "training/data/synthetic.jsonl"]:
    p = Path(f)
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip():
                try:
                    d = json.loads(line)
                    SEEN_QUERIES.add(d.get("text", "").strip().casefold())
                except Exception:
                    pass

SEEN_QUERIES.discard("")

for label, topics in LABELS_AND_TOPICS.items():
    for topic in topics:
        prompt = PROMPT.format(topic=topic, label=label)
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=t.GenerateContentConfig(temperature=0.9, max_output_tokens=256),
            )
            for line in resp.text.strip().split("\n"):
                q = line.strip().strip('"').strip("'").strip("- ").strip()
                if q and len(q) > 5 and q.casefold() not in SEEN_QUERIES:
                    SEEN_QUERIES.add(q.casefold())
                    records.append({"text": q, "label": label})
            print(f"  [{label:>3}] {topic[:40]:40s} → {len(resp.text.splitlines())} queries")
        except Exception as e:
            print(f"  [{label:>3}] {topic[:40]:40s} → FAILED: {e}")

with output_path.open("w") as f:
    for rec in records:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

print(f"\nGenerated {len(records)} → {output_path}")
print("Distribution:")
for k, v in Counter(r["label"] for r in records).most_common():
    print(f"  {k}: {v}")
