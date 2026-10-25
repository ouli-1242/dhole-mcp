"""P03 库尔班 · 运维 L1 —— start 三路线 L1 照走。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(3, "库尔班·运维")
p.call("cred_defaults", {"set": {"allow_private": True}})
p.set_scenario("S1-start 篇路线")
r = p.call("sys_guide", {"topic": "start"})
p.observe("start", r, "summary")
p.pause(6)
p.set_scenario("S2-盯站路线(feed)")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 3})
p.observe("feed", r, "summary")
p.pause(6)
p.set_scenario("S3-watch 旁支(CLI)")
import subprocess
add = subprocess.run(["dhole", "watch", "add", "http://127.0.0.1:8399/changing"], capture_output=True, text=True)
run = subprocess.run(["dhole", "watch", "run"], capture_output=True, text=True, timeout=120)
print("   watch run exit:", run.returncode, "|", ((run.stdout or "") + (run.stderr or "")).strip().splitlines()[-1][:120])
subprocess.run(["dhole", "watch", "remove", "http://127.0.0.1:8399/changing"], capture_output=True, text=True)
print("P03 DONE")
