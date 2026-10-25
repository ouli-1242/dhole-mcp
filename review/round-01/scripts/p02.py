"""P02 老周 · 后端工程师 · L3（缺陷猎手）—— round-01 真实模拟。"""
import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(2, "老周·后端")

# S1 建图 + 爬子树（服务刚重启过，私网门恢复默认 —— 撞门后按提示放行）
p.set_scenario("S1-建图爬取")
r = p.call("web_discover", {"url": ROOT + "/", "sitemap": True, "max_pages": 5})
p.observe("discover", r, "summary", "plan_id", "links", "resume_token")
if r.get("error") and "private" in str(r.get("error")):
    p.pause(8, "读懂拒绝后放行")
    p.call("cred_defaults", {"set": {"allow_private": True}})
    p.pause(4)
    r = p.call("web_discover", {"url": ROOT + "/", "sitemap": True, "max_pages": 5})
    p.observe("discover retry", r, "summary", "plan_id", "links")
p.pause(10, "重调用间隔")
r = p.call("web_crawl", {"url": ROOT + "/", "max_pages": 4, "max_depth": 1,
                         "search": "详情", "max_content_chars_per": 1500})
p.observe("crawl", r, "summary", "pages_crawled", "resume_token")
p.pause(10)

# S2 边界四连（故意写错）
p.set_scenario("S2-边界")
r = p.call("web_read", {"url": ROOT + "/about", "format": "markdown"})
p.observe("unknown param", r, "error", "diagnosis.code")
r = p.call("web_read", {"url": ROOT + "/about",
                        "options": {"max_content_chars": 500}})
p.observe("options bag", r, "error", "diagnosis.code")
r = p.call("web_read", {"url": ROOT + "/article", "max_content_chars": 999999})
p.observe("over cap", r, "error", "diagnosis.code")
r = p.call("web_read", {"url": ROOT + "/longaside", "offset": 500,
                        "max_content_chars": 800})
p.observe("offset", r, "content_ok", "total_extracted_chars", "next_offset")
p.pause(30, "场景间隔")

# S3 链：crawl(挑战) -> sys_state -> recall
p.set_scenario("S3-链路")
r = p.call("web_crawl", {"url": ROOT + "/", "max_pages": 6, "max_depth": 1,
                         "search": "挑战"})
p.observe("crawl challenge", r, "summary", "pages_crawled")
p.pause(6)
r = p.call("sys_state", {})
p.observe("census", r, "cache", "sessions", "identities")
p.pause(6)
r = p.call("web_recall", {"query": "挑战 抄写", "limit": 5})
p.observe("recall", r, "summary")
p.rows(r, "results", 5)
p.pause(30, "场景间隔")

# S4 续爬语义（collections 扇出 -> resume）
p.set_scenario("S4-续爬")
r = p.call("web_crawl", {"url": ROOT + "/collections", "max_pages": 3,
                         "max_depth": 1})
p.observe("crawl 1", r, "summary", "pages_crawled", "resume_token", "next_action")
tok = r.get("resume_token") or (r.get("metadata") or {}).get("resume_token")
print("      resume_token:", (tok or "NONE")[:60])
if tok:
    p.pause(10, "重调用间隔")
    r = p.call("web_crawl", {"resume_token": tok, "max_pages": 6})
    p.observe("crawl resumed", r, "summary", "pages_crawled")

print("P02 DONE")
