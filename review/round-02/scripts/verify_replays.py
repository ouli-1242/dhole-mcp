"""P5 final evidence pass: re-run W1/W2/W4/W5 live and save raw outputs to
sessions/verify/ so the verification doc cites files, not memory. (W3 has its
own probe_w3c.py run teed separately.)"""
import json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from driver import PersonaSim

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "sessions", "verify")
os.makedirs(OUT, exist_ok=True)

def save(name, obj):
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    print("  saved", os.path.normpath(path))

p = PersonaSim(96, "verify")
p.call("cred_defaults", {"set": {"allow_private": True}})

print("== W1 R02-002: wayback capture-index guard ==")
r = p.call("web_read", {"url": "https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32016R0679",
                        "timeout": 60000})
t = str(r)
save("w1-archive.json", r)
print("  content_ok:", re.findall(r"content_ok[\"']?: ?\w+", t)[:2])
print("  capture-index note:", re.findall(r"[^\"']{0,80}capture[ -]index[^\"']{0,120}", t)[:1] or "NONE")

print("== W2 R02-003: 401 lever names cred_defaults ==")
r = p.call("web_read", {"url": "https://wsj.com/articles/tech-companies-data-privacy-11634996753"})
t = str(r)
save("w2-401.json", r)
print("  recover_with:", re.findall(r"recover_with[\"']?: ?[^,}]{0,140}", t)[:1] or "EMPTY")
print("  status:", re.findall(r"[\"']?status[\"']?: ?\d+", t)[:1])

print("== W4 R02-007: recall merges url variants ==")
p.call("web_crawl", {"url": "http://127.0.0.1:8399/collections", "max_pages": 2, "cache_ttl": 0})
time.sleep(1)
p.call("web_crawl", {"url": "http://127.0.0.1:8399/collections", "max_pages": 1, "cache_ttl": 0})
r = p.call("web_recall", {"query": "collections product pages"})
save("w4-recall.json", r)
t = str(r)
print("  variants:", re.findall(r"variants[\"']?: ?\d+", t)[:3])
print("  collections rows:", t.count("/collections"))

print("== W5 R02-001: watch private-gate hint in error row ==")
subprocess.run(["dhole", "watch", "add", "http://127.0.0.1:8399/changing"],
               capture_output=True, text=True)
run = subprocess.run(["dhole", "watch", "run"], capture_output=True, text=True, timeout=120)
blob = (run.stdout or "") + (run.stderr or "")
with open(os.path.join(OUT, "w5-watch.txt"), "w", encoding="utf-8") as f:
    f.write(blob)
print("  hint:", re.findall(r"watch runs in its own process[^·\n]{0,160}", blob)[:1] or "ABSENT")
subprocess.run(["dhole", "watch", "remove", "http://127.0.0.1:8399/changing"],
               capture_output=True, text=True)
print("  (watchlist cleaned)")
