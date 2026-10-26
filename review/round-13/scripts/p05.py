"""P05 米酒儿 · 京剧琴师 L1 —— 链⑤upload 同 call submit（寿命句）→跨 call 复用被拒→feed→state。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
fdir.mkdir(parents=True, exist_ok=True)
score = fdir / "qinpu.txt"
score.write_text("工尺谱：上尺工凡六五乙\n西皮二黄板式对照表\n", encoding="utf-8")

p = PersonaSim(5, "米酒儿·京剧琴师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-act upload+submit 同 call")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/upload", "timeout": 60000,
                       "actions": [
                           {"upload": {"selector": "#doc", "files": [str(score)]}},
                           {"click": {"selector": "#send"}},
                           {"read": {"selector": "#receipt"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:60]!r} lifetime={str(a.get('lifetime'))[:60]}")

p.pause(6)
p.set_scenario("S2-跨 call 复用上传（寿命句应教同 call 提交）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/upload", "timeout": 60000,
                       "actions": [
                           {"upload": {"selector": "#doc", "files": [str(score)]}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} lifetime={str(a.get('lifetime'))[:110]}")

p.pause(6)
p.set_scenario("S3-feed 收尾")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 2})
print(f"   | {str(r.get('summary'))[:120]}")

p.pause(6)
p.set_scenario("S4-state 复盘")
r = p.call("sys_state", {})
print(f"   message: {str(r.get('message'))[:130]}")
print("P05 DONE")
