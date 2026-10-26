"""P12 陆知微补条 · check 第二发（静态页应 unchanged）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(12, "陆知微·专利审查员补条")
p.call("cred_defaults", {"set": {"allow_private": True}})

U = "http://127.0.0.1:8399/work/2?cb=p02-static"

p.set_scenario("T1-check 记基线")
r = p.call("web_read", {"urls": [U], "extraction_type": "check"})
print(f"   {str(r.get('summary'))[:160]}")

p.pause(5)
p.set_scenario("T2-check 再问一次（应 unchanged）")
r = p.call("web_read", {"urls": [U], "extraction_type": "check"})
print(f"   {str(r.get('summary'))[:200]}")
for x in r.get("results") or []:
    print("   -", str({k: x.get(k) for k in ('url', 'changed', 'status', 'note') if x.get(k) is not None})[:240])
print("P12 DONE")
