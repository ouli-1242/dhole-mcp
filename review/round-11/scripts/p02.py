"""P02 白景舟3 · 珠宝鉴定师 L3 猎手 —— web_discover 深水（地图完整性/断链/depth）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(2, "白景舟3·珠宝鉴定师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-/collections 地图")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/collections"})
rows = r.get("results") or r.get("links") or r.get("pages") or []
urls = [str(x.get("url")) for x in rows] if isinstance(rows, list) else []
print(f"   summary: {str(r.get('summary'))[:130]}")
print(f"   行数: {len(urls)} | 采样: {urls[:4]}")

p.pause(6)
p.set_scenario("S2-行可点性(discover→read)")
if urls:
    r2 = p.call("web_read", {"url": urls[0], "cache_ttl": 0})
    print(f"   top 行 read: ok={r2.get('content_ok')} | {str(r2.get('summary'))[:90]}")

p.pause(6)
p.set_scenario("S3-depth 参数语义")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/collections", "max_depth": 0})
print(f"   depth0: {str(r.get('summary'))[:130]}")

p.pause(6)
p.set_scenario("S4-404 页 discover(断链现场)")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/no-such-page"})
print(f"   err: {str(r.get('error'))[:150] or str(r.get('summary'))[:130]}")
print("P02 DONE")
