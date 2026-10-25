"""P07 续：查 sys_guide 动作参考，找链内变量语义。"""
import sys, json, re
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT
p = PersonaSim(7, "阿凯·安全")
p.seq = 10
p.set_scenario("S3c-查手册")
r = p.call("sys_guide", {"topic": "actions"})
txt = json.dumps(r.get("content") or r, ensure_ascii=False)
i = txt.find("fill")
print("      fill ctx:", txt[max(0,i-300):i+500] if i >= 0 else txt[:600])
i2 = txt.find("value_from")
if i2 >= 0:
    print("      value_from ctx:", txt[max(0,i2-200):i2+400])
print("P07C DONE")
