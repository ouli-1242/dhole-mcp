"""P3 探针 B —— urls/headers/nested_fields/枚举数组等顶层容器元素类型错。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

NOTE = Path(__file__).resolve().parent.parent / "sessions" / "p12-note.txt"
NOTE.write_text("第一行\n第二行\n第三行\n", encoding="utf-8")

p = PersonaSim(12, "P3 探针B")
p.call("cred_defaults", {"set": {"allow_private": True}})


def show(label, r, *fields):
    print(f"   {label}: err={str(r.get('error'))[:200]!r}")
    for f in fields:
        v = r
        for k in f.split("."):
            v = (v or {}).get(k) if isinstance(v, dict) else None
        if v is not None:
            print(f"      {f}=", str(v)[:120])


p.set_scenario("B1-urls 元素类型混杂")
show("B1", p.call("web_read", {"urls": [ROOT + "/article?cb=p12", 123, None]}),
     "summary", "total_extracted_chars")

p.pause(2)
p.set_scenario("B2-headers 值非字符串")
show("B2", p.call("web_read", {"url": ROOT + "/article?cb=p12b",
                               "headers": {"X-Test": 123}}))

p.pause(2)
p.set_scenario("B3-nested_fields 元素非字符串")
show("B3", p.call("web_read", {"url": ROOT + "/article?cb=p12c",
                               "nested_selector": "body", "nested_fields": [123]}))

p.pause(2)
p.set_scenario("B4-nested_fields 字符串")
show("B4", p.call("web_read", {"url": ROOT + "/article?cb=p12d",
                               "nested_selector": "body", "nested_fields": "a,b"}))

p.pause(2)
p.set_scenario("B5-search engines 是数字数组")
show("B5", p.call("web_search", {"query": "机械键盘", "engines": [123]}))

p.pause(3)
p.set_scenario("B6-crawl path_include 是字符串")
show("B6", p.call("web_crawl", {"url": ROOT + "/collections?cb=p12", "path_include": "/work",
                                "max_pages": 2, "timeout": 30000}),
     "summary")

p.pause(2)
p.set_scenario("B7-doc_parse offset 负数")
show("B7", p.call("doc_parse", {"file_path": str(Path(__file__).resolve().parent.parent
                                                   / "sessions" / "p12-note.txt"),
                                "offset": -5}))

p.pause(2)
p.set_scenario("B8-feed since 错格式")
show("B8", p.call("feed_read", {"urls": [ROOT + "/feed.xml"], "since": "yesterday"}))

p.pause(2)
p.set_scenario("B9-state clear 未知令牌")
show("B9", p.call("sys_state", {"clear": ["nope"], "confirm": True}))

p.pause(2)
p.set_scenario("B10-cred_defaults unset 未知键")
show("B10", p.call("cred_defaults", {"unset": ["nope"]}))

p.pause(2)
p.set_scenario("B11-web_request body 是列表")
show("B11", p.call("web_request", {"url": ROOT + "/checkout", "method": "POST",
                                   "body": ["a"], "confirm": True}))
