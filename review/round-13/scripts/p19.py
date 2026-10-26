"""P19 陆沉舟补条 · web_research 覆盖补发（P07 旁支）→ 证据包逐源带 why。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(19, "陆沉舟·隧道测量员补条")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-research 一问一证据包")
r = p.call("web_research", {"query": "Model Context Protocol specification",
                            "max_sources": 2, "per_source_chars": 1500,
                            "timeout": 120000})
srcs = r.get("sources") or []
print(f"   sources={len(srcs)} failed={len(r.get('failed') or [])} "
      f"more_hits={len(r.get('more_hits') or [])}")
for x in srcs[:3]:
    print("   -", str({k: x.get(k) for k in ('n', 'url', 'title', 'chars', 'content_ok') if k in x})[:160])
for x in (r.get('failed') or [])[:3]:
    print("   !", str(x)[:160])
print("P19 DONE")
