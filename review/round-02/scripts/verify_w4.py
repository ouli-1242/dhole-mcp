import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from driver import PersonaSim

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "sessions", "verify")

p = PersonaSim(95, "verify-w4")
p.call("cred_defaults", {"set": {"allow_private": True}})
r1 = p.call("web_crawl", {"url": "http://127.0.0.1:8399/collections", "max_pages": 3, "cache_ttl": 0})
t1 = str(r1)
print("crawl-1 urls seen:", re.findall(r"[\"']?url[\"']?: ?[\"']http[^\"']+", t1)[:6])
time.sleep(1)
p.call("web_crawl", {"url": "http://127.0.0.1:8399/collections", "max_pages": 1, "cache_ttl": 0})
r = p.call("web_recall", {"query": "collections"})
t = str(r)
with open(os.path.join(OUT, "w4-recall.json"), "w", encoding="utf-8") as f:
    json.dump(r, f, ensure_ascii=False, indent=1, default=str)
print("recall variants:", re.findall(r"variants[\"']?: ?\d+", t)[:4])
print("rows with /collections:", t.count("/collections"))
print("stale flags:", re.findall(r"stale[\"']?: ?\w+", t)[:4])
