import json, sys
d = json.load(sys.stdin)
r = d['result']
items = r.get('items') or r.get('results') or []
print(f"  count: {len(items)}")
for i, x in enumerate(items):
    url = x.get('url') or x.get('input_url') or '?'
    status = x.get('status', '?')
    backend = x.get('fetch_backend', '?')
    content_len = len(x.get('page_content', ''))
    print(f"  [{i+1}] status={status} backend={backend} len={content_len} url={url}")
print(f"  guidance: {d.get('agent_guidance', [{}])[0].get('message', '(none)')[:100]}")
