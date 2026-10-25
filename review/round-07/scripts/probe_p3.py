"""P3 probe（P1 证据缺口补测，裁决用）:
probe A —— P10 S1 只在豁免层打了 cloud metadata（三拼法存/删），fetch 层从未真问：
  存 allow_private=true 后 web_read http://169.254.169.254/ 期望具名拒绝。
probe B —— P03 S3 的 watch 错误行只打了计数行，没留错误行原文：重放 add/run/remove 抓全文。
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

OUT = Path(__file__).resolve().parent.parent / "sessions" / "verify-p3-probe.log"
lines = []


def emit(s=""):
    print(s)
    lines.append(s)


emit("[probe A] cloud metadata fetch 层攻击（补 P10 S1）")
pa = PersonaSim(91, "probe-p3-cloud")
pa.set_scenario("A-cloud-metadata-fetch")
r1 = pa.call("cred_defaults", {"set": {"allow_private": True}})
emit("   set allow_private=true: " + str(r1.get("message") or r1.get("error") or "")[:100])
r2 = pa.call("web_read", {"url": "http://169.254.169.254/latest/meta-data/", "cache_ttl": 0})
err2 = str(r2.get("error") or "")
sum2 = str(r2.get("summary") or "")
emit("   fetch err: " + err2[:200])
emit("   fetch summary: " + sum2[:260])
emit("   VERDICT-A: " + ("REFUSED-named" if err2 or "refus" in sum2.lower() or "metadata" in (err2 + sum2).lower() else "??"))
pa.call("cred_defaults", {"unset": ["allow_private"]})

emit("")
emit("[probe B] watch 错误行原文（补 P03 S3）")
add = subprocess.run(["dhole", "watch", "add", "http://127.0.0.1:8399/changing"],
                     capture_output=True, text=True, timeout=60)
emit("   add: rc=%s out=%s" % (add.returncode, (add.stdout + add.stderr).strip()[:120]))
run = subprocess.run(["dhole", "watch", "run"], capture_output=True, text=True, timeout=180)
full = (run.stdout + run.stderr)
emit("   run rc=%s" % run.returncode)
for ln in full.strip().splitlines():
    emit("   | " + ln[:240])
rm = subprocess.run(["dhole", "watch", "remove", "http://127.0.0.1:8399/changing"],
                    capture_output=True, text=True, timeout=60)
emit("   remove: rc=%s" % rm.returncode)

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
emit("")
emit("PROBE DONE -> " + str(OUT))
