"""P05 霍砚 · 创业者 L3 猎手 —— discover→crawl→web_request echo→watch 真跨进程（R02-001 回归）。"""
import subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(5, "霍砚·创业者")

p.set_scenario("S1-竞品站先出地图")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/collections", "timeout": 30000})
p.observe("discover", r, "summary")
p.rows(r, "pages", 6, 100)
p.pause(8)

p.set_scenario("S2-往深处爬找接口/定价页")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/collections", "max_pages": 6, "cache_ttl": 0})
p.observe("crawl", r, "summary")
p.pause(7)

p.set_scenario("S3-接口形状本地验证(POST)")
r = p.call("web_request", {"method": "POST", "url": "http://127.0.0.1:8399/login",
                           "body": {"username": "demo", "password": "wrong"},
                           "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                           "timeout": 20000})
p.observe("post-login", r, "status", "summary")
p.pause(8)

p.set_scenario("S4-盯竞品页(真实跨进程 watch)")
add = subprocess.run(["dhole", "watch", "add", "http://127.0.0.1:8399/changing"],
                     capture_output=True, text=True, timeout=60)
print("      watch add:", (add.stdout or add.stderr or "").strip()[:160])
run = subprocess.run(["dhole", "watch", "run"], capture_output=True, text=True, timeout=120)
blob = (run.stdout or "") + (run.stderr or "")
print("      watch run exit:", run.returncode)
for line in blob.splitlines():
    if "private" in line or "watch runs in its own process" in line:
        print("      row:", line.strip()[:220])
        break
print("      (回归 R02-001: 期望错误行带 watch 进程语境出路)")
subprocess.run(["dhole", "watch", "remove", "http://127.0.0.1:8399/changing"],
               capture_output=True, text=True)
print("      (watchlist cleaned)")
p.pause(8)

p.set_scenario("S5-自查状态")
r = p.call("sys_state", {})
p.observe("state", r, "summary")
print("P05 DONE")
