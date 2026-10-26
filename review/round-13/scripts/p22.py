"""P22 P3 复现探针 · C6 写→回放同 URL 对照；C7 显式 selector 收窄误判。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(22, "P3复现·审查")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("C6a-css 0匹配 首次 live（应入缓存）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=r13c",
                        "css_selector": "#no-such"})
s = str(r.get('summary') or '')
print(f"   cached={r.get('cached')} chars={r.get('total_extracted_chars')} "
      f"note={'matched 0 elements' in s}")

p.pause(4)
p.set_scenario("C6b-同 URL 同 selector 二次（应回放）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=r13c",
                        "css_selector": "#no-such"})
s = str(r.get('summary') or '')
print(f"   cached={r.get('cached')} chars={r.get('total_extracted_chars')} "
      f"note={'matched 0 elements' in s}")

p.pause(6)
p.set_scenario("C7-显式 selector 收窄（h1）→ content_ok/error")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=r13d",
                        "css_selector": "h1"})
print(f"   content_ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"error={str(r.get('error'))[:120]}")
print(f"   content={str(r.get('content'))[:80]}")
print("P22 DONE")
