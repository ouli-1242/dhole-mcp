"""P07 续：topic=call 里找 fill 链内取值语义。"""
import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(7, "阿凯·安全")
p.seq = 11
p.set_scenario("S3d-查call手册")
r = p.call("sys_guide", {"topic": "call"})
txt = json.dumps(r.get("content") or r.get("_raw") or r, ensure_ascii=False)
print("      len:", len(txt))
for kw in ("fill", "challenge", "read 回执", "前一个动作"):
    i = txt.find(kw)
    if i >= 0:
        print("      [%s] ...%s..." % (kw, txt[max(0,i-250):i+450].replace("\\n", " ")))
        break
print("P07D DONE")
