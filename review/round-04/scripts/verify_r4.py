"""P5 复验：真重启后 (W1) iframe 超时提示 + (W2) watch 出路句。"""
import subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(40, "verify-r4")
p.call("cred_defaults", {"set": {"allow_private": True}})

print("== W1 R04-001: iframe 页裸 selector 超时要提 frame ==")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame", "actions": [
    {"fill": {"selector": "#note", "text": "x"}},
], "timeout": 40000})
for st in ((r.get("metadata") or {}).get("actions") or []):
    err = str(st.get("error") or "")
    print("  step err 含 iframe 提示:", "iframe" in err and '"frame"' in err)
    print("  原文:", err[:240])

print("== W2 R04-002: watch 出路句点名 watch 进程 env ==")
subprocess.run(["dhole", "watch", "add", "http://127.0.0.1:8399/changing"],
               capture_output=True, text=True)
run = subprocess.run(["dhole", "watch", "run"], capture_output=True, text=True, timeout=120)
blob = (run.stdout or "") + (run.stderr or "")
hit = [l for l in blob.splitlines() if "watch process's own environment" in l]
print("  出路句新前题在场:", bool(hit))
if hit:
    print("  原文:", hit[0].strip()[:250])
subprocess.run(["dhole", "watch", "remove", "http://127.0.0.1:8399/changing"],
               capture_output=True, text=True)
print("  (watchlist cleaned)")
print("VERIFY DONE")
