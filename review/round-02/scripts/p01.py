"""P01 何工 · 运维/SRE · L3 —— round-02 真实模拟（watch/对账/边界）。"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(1, "何工·SRE")
p.set_scenario("S0-开场认知")
r = p.call("sys_state", {})
p.observe("census", r, "summary")
p.pause(6, "看回执")

p.set_scenario("S1-watch 靶站变更")
# 私网门第一撞（9202 新进程，cred_defaults 清零——真实摩擦路线）
r = p.call("cred_defaults", {"set": {"allow_private": True}})
p.observe("stored", r, "changed")
out = subprocess.run(["dhole", "watch", "add", "http://127.0.0.1:8399/changing"],
                     capture_output=True, text=True, timeout=60)
print("[watch add] rc=%s out=%s" % (out.returncode, (out.stdout + out.stderr)[:200]))
out = subprocess.run(["dhole", "watch", "run"], capture_output=True, text=True, timeout=120)
print("[watch run #1] rc=%s (基线) out=%s" % (out.returncode, (out.stdout + out.stderr)[-200:]))
out = subprocess.run(["dhole", "watch", "run"], capture_output=True, text=True, timeout=120)
print("[watch run #2] rc=%s (/changing 每次都变) out=%s" % (out.returncode, (out.stdout + out.stderr)[-300:]))
p.pause(10)

p.set_scenario("S2-重启对账")
r = p.call("sys_state", {"show": ["cache", "sessions", "identities"]})
p.observe("before", r, "summary")
p.pause(4, "读者对账")

p.set_scenario("S3-crawl 边界 40 页")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/collections",
                         "max_pages": 40, "deadline_ms": 30000, "cache_ttl": 0})
p.observe("crawl40", r, "summary", "duration_ms")
p.rows(r, "pages", 3, 110)
p.pause(15)

p.set_scenario("S4-熔断冷却语义")
r = p.call("web_search", {"query": "水泥 标号 含义", "engines": ["so360"], "max_results": 3})
p.observe("so360-cooling", r, "error", "diagnosis.code", "engine_detail")
p.pause(10)

print("P01 DONE")
