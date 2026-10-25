"""P07 越千帆5 · 飞行签派员 L3 猎手 —— 第一拒全集抽样（13 颗形状错）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(7, "越千帆5·飞行签派员")
p.call("cred_defaults", {"set": {"allow_private": True}})

cases = [
    ("web_read", {"url": 123}, "url 类型"),
    ("web_act", {"url": "http://127.0.0.1:8399/about", "actions": []}, "actions 空"),
    ("web_search", {"query": "x", "site": "a b c"}, "site 含空格"),
    ("web_recall", {}, "query 缺"),
    ("web_research", {"max_sources": 2}, "query 缺"),
    ("web_crawl", {"max_pages": 2}, "url 缺"),
    ("web_discover", {}, "url 缺"),
    ("web_request", {"method": "POST"}, "url 缺"),
    ("feed_read", {"urls": []}, "urls 空"),
    ("doc_parse", {}, "file_path 缺"),
    ("cred_defaults", {"set": {"nope_key": 1}}, "未知键"),
    ("sys_guide", {"topic": "nope"}, "未知 topic"),
]
for tool, args, label in cases:
    r = p.call(tool, args)
    err = str(r.get("error") or "")
    na = str(r.get("next_action") or "")
    has_way = bool(na) or "Valid" in err or "must" in err or "Pass" in err or "one of" in err
    print(f"   {label:14s}: 前缀={'invalid_request:' in err} 出路={'Y' if has_way else 'N'} | {err[:90]}")
    p.pause(3)
print("P07 DONE")
