"""P08 闻岁酿 · 酒侍 L2 —— 链条⑧parse 多 sheet/GBK→feed 混发分账→sys_state 盘点（r11 必测3/4）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
fdir.mkdir(parents=True, exist_ok=True)

# 双 sheet 酒单（r11 在册观察：多 sheet 小计是否在场）
try:
    import openpyxl
    wb = openpyxl.Workbook()
    red = wb.active
    red.title = "红酒"
    red.append("酒名,产区,年份".split(","))
    red.append(["波尔多", "法国", "2018"])
    red.append(["巴罗洛", "意大利", "2016"])
    white = wb.create_sheet("白酒")
    white.append("酒名,产区,年份".split(","))
    white.append(["夏布利", "勃艮第", "2020"])
    wb.save(fdir / "wine_list.xlsx")
except Exception as e:
    print(f"   (xlsx 构造失败: {e})")

gbk = fdir / "jiuzhuang_gbk.csv"
gbk.write_bytes("酒庄,产区\n张裕,烟台\n长城,沙城\n".encode("gbk"))

p = PersonaSim(8, "闻岁酿·酒侍")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-parse 双 sheet 酒单")
r = p.call("doc_parse", {"file_path": str(fdir / "wine_list.xlsx")})
print(f"   | {str(r.get('summary'))[:130]}")
txt = "".join(str(c.get("text", "")) for c in r.get("content") or [] if isinstance(c, dict))
print(f"   红酒表在场={'红酒' in txt} 白酒表在场={'白酒' in txt}")

p.pause(6)
p.set_scenario("S2-parse GBK 酒庄表（保真）")
r = p.call("doc_parse", {"file_path": str(gbk)})
txt = "".join(str(c.get("text", "")) for c in r.get("content") or [] if isinstance(c, dict))
print(f"   张裕={'张裕' in txt} 沙城={'沙城' in txt} | {str(r.get('summary'))[:90]}")

p.pause(6)
p.set_scenario("S3-feed 混发分账（好源+404+拒连）")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml",
                                  "http://127.0.0.1:8399/feed-404.xml",
                                  "http://127.0.0.1:9399/feed.xml"],
                          "max_items": 3})
rows = r.get("feeds") or r.get("results") or []
for x in rows:
    items = x.get("items") or []
    print(f"     - {str(x.get('url'))[:50]} items={len(items)} err={str(x.get('error') or '-')[:70]}")

p.pause(6)
p.set_scenario("S4-sys_state 盘点")
r = p.call("sys_state", {})
print(f"   | {str(r.get('summary'))[:140]}")
print("P08 DONE")
