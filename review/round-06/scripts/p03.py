"""P03 乐正绫 · 编辑 L1 —— html/text/article 三档形态对照。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(3, "乐正绫·编辑")
p.call("cred_defaults", {"set": {"allow_private": True}})
for t in ("html", "text", "article"):
    p.set_scenario(f"extraction_type={t}")
    r = p.call("web_read", {"url": f"http://127.0.0.1:8399/article?t={t}", "cache_ttl": 0,
                            "extraction_type": t, "timeout": 30000})
    c = r.get("content"); c = ''.join(c) if isinstance(c, list) else str(c)
    has_tags = "<" in c[:400] and ">" in c[:400]
    print(f"   {t}: len={len(c)} 含标签={has_tags} | 头: {c[:70].replace(chr(10),' ')}")
    p.observe(f"{t}", r, "content_ok")
    p.pause(6)
p.set_scenario("求助")
r = p.call("sys_guide", {"topic": "call"})
p.observe("guide", r, "summary")
print("P03 DONE")
