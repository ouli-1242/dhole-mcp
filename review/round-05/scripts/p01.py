"""P01 邵岐 · SRE L3 —— 故障三连：假域名/死端口/慢站，逐层账目。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(1, "邵岐·SRE")
p.set_scenario("S1-假域名(全档都该空手)")
r = p.call("web_read", {"url": "http://no-such-domain-audit-9x7.example.com/", "timeout": 30000})
p.observe("fake-domain", r, "error", "summary")
p.pause(7)
p.set_scenario("S2-死端口(连接拒绝)")
r = p.call("web_read", {"url": "http://127.0.0.1:9391/", "timeout": 20000})
p.observe("dead-port", r, "error", "summary")
p.pause(7)
p.set_scenario("S3-超时梯子(timeout=5000 的真实分账)")
r = p.call("web_read", {"url": "http://no-such-domain-audit-9x7.example.com/", "timeout": 5000})
p.observe("tight-timeout", r, "error", "summary")
p.pause(7)
p.set_scenario("S4-降级链账目(外网真站带 archive)")
r = p.call("web_read", {"url": "https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32016R0679",
                        "timeout": 60000})
p.observe("ladder", r, "error", "summary")
print("P01 DONE")
