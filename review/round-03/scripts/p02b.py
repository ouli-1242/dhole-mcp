"""P02b 沙青·续爬(补):跨会话照抄 next_action 里的 resume 调用(R01-003 回归)。"""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

tok = json.load(open(Path(__file__).resolve().parent.parent / "sessions" / "persona-02" / "02_web_crawl.json", encoding="utf-8"))["next_resume_token"]
url = json.load(open(Path(__file__).resolve().parent.parent / "sessions" / "persona-02" / "02_web_crawl.json", encoding="utf-8"))["start_url"]

p = PersonaSim(12, "沙青·续爬2")  # 新会话,照抄 next_action
r = p.call("web_crawl", {"url": url, "resume_token": tok, "max_pages": 9, "cache_ttl": 0})
p.observe("resume", r, "summary")
p.rows(r, "pages", 5, 110)
print("P02b DONE")
