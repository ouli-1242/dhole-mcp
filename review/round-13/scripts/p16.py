"""P16 米酒儿补条 · upload + click confirm 真提交 → #receipt=received (N bytes)。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
fdir.mkdir(parents=True, exist_ok=True)
score = fdir / "qinpu2.txt"
score.write_text("工尺谱续：上尺工凡六五乙\n二黄慢板 西皮快板\n", encoding="utf-8")

p = PersonaSim(16, "米酒儿·京剧琴师补条")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-upload→click confirm→receipt")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/upload", "timeout": 60000,
                       "actions": [
                           {"upload": {"selector": "#doc", "files": [str(score)]}},
                           {"click": {"selector": "#send", "confirm": True}},
                           {"read": {"selector": "#receipt"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:60]!r} "
          f"err={str(a.get('error'))[:80]}")
print("P16 DONE")
