"""P10 尉迟雪 · 旅游博主 L1 —— QR/图片页 + 跨会话找回。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(10, "尉迟雪·博主")
p.set_scenario("S1-读带 QR 的页")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_read", {"url": "http://127.0.0.1:8399/qr", "timeout": 30000})
p.observe("qr", r, "content_ok", "total_extracted_chars")
c = r.get("content"); c = ''.join(c) if isinstance(c, list) else str(c)
print("   正文:", c[:150].replace("\n", " "))
p.pause(7)
p.set_scenario("S2-搜目的地攻略")
r = p.call("web_search", {"query": "泉州 两日游 路线 攻略", "max_results": 5})
p.rows(r, "results", 3, 110)
p.pause(7)
p.set_scenario("S3-跨会话找回")
pb = PersonaSim(10, "尉迟雪·下周")
r = pb.call("web_recall", {"query": "二维码 扫码"})
p.rows(r, "results", 3, 120)
print("P10 DONE")
