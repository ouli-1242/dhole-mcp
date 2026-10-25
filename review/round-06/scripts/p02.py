"""P02 邝云汉 · 翻译 L3 —— focus 三档对照(页尾冷词保真)。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(2, "邝云汉·翻译")
p.call("cred_defaults", {"set": {"allow_private": True}})
for mode in ("extractive", "abstractive", "auto"):
    p.set_scenario(f"focus={mode}")
    r = p.call("web_read", {"url": f"http://127.0.0.1:8399/longaside?f={mode}", "cache_ttl": 0,
                            "focus": "缓存 键", "focus_mode": mode, "timeout": 45000})
    c = r.get("content"); c = ''.join(c) if isinstance(c, list) else str(c)
    s = str(r.get("summary"))
    import re
    eng = re.findall(r"focus[_ ]engine[\": ]+([a-z_]+)", s) or re.findall(r"(rerank|bm25|neural)", s)
    print(f"   {mode}: chars={len(c)} 键词在={('缓存' in c and '键' in c)} engine线索={eng[:1]}")
    print("   summary 头:", s[:110])
    p.pause(6)
p.set_scenario("sys_state")
r = p.call("sys_state", {})
p.observe("state", r, "summary")
print("P02 DONE")
