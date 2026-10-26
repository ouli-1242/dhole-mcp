"""生成 round-16 多语言页面站（8398）的静态文件与本地数据文件。"""
import csv
from pathlib import Path

site = Path(__file__).resolve().parent.parent / "site"
site.mkdir(parents=True, exist_ok=True)

PAGE = """<!doctype html>
<html lang="{lang}"><head><meta charset="{charset}">
<title>{title}</title></head><body>
<h1>{title}</h1>
<p>{body}</p>
<p>End of the {title} sample.</p>
</body></html>
"""

( site / "ar.html").write_text(PAGE.format(
    lang="ar", charset="utf-8", title="اختبار الترميز العربي",
    body="هذا نص عربي طويل لاختبار اتجاه الكتابة من اليمين إلى اليسار. "
         "يحتوي على أرقام 12345 وكلمات إنجليزية mixed in."), encoding="utf-8")

(site / "ja.html").write_text(PAGE.format(
    lang="ja", charset="utf-8", title="日本語の文字化けテスト",
    body="これは日本語の長い本文です。ひらがな、カタカナ、漢字、そして絵文字 😀🚀 も含みます。"
         "HTTP の文字コード判定が正しく行われるかを確認します。"), encoding="utf-8")

(site / "ko.html").write_text(PAGE.format(
    lang="ko", charset="utf-8", title="한국어 인코딩 테스트",
    body="이것은 한국어 본문입니다. 한글 자모와 영문 mixed text, 숫자 2026 을 포함합니다."),
    encoding="utf-8")

(site / "ru.html").write_text(PAGE.format(
    lang="ru", charset="utf-8", title="Тест кодировки",
    body="Это длинный русский текст для проверки кодировки. Кириллица, цифры 42 и "
         "латиница mixed."), encoding="utf-8")

(site / "emoji.html").write_text(PAGE.format(
    lang="zh", charset="utf-8", title="表情符号与组合字符测试",
    body="这里有 emoji 😀🚀👩‍💻 与组合字符 e\u0301、全角ＡＢＣ、零宽字符\u200b，"
         "以及一段足够长的中文正文用于抽取。"), encoding="utf-8")

# GBK 页面：服务端不带 charset，靠 meta 声明；检验探测链
(site / "gbk.html").write_bytes(PAGE.format(
    lang="zh", charset="gbk", title="国标编码页面",
    body="这是一个 GBK 编码的页面，正文包含中文与数字 2026。").encode("gbk"))

# Latin-1 页面：带重音字符
(site / "latin1.html").write_bytes(PAGE.format(
    lang="fr", charset="iso-8859-1", title="Encodage français",
    body="Voici un texte français avec des accents : é è ê à ç ù. "
         "Un nombre : 2026.").encode("latin-1"))

# 混合页：CJK + RTL + emoji + 链接
(site / "mixed.html").write_text(
    "<!doctype html><html lang='zh'><head><meta charset='utf-8'><title>混合语言页</title></head>"
    "<body><h1>混合语言页</h1><p>中文段落，English sentence, العربية, 日本語, 😀.</p>"
    "<a href='/ar.html'>العربية</a> <a href='/ja.html'>日本語</a></body></html>",
    encoding="utf-8")

files = Path(__file__).resolve().parent.parent / "sessions" / "files"
files.mkdir(parents=True, exist_ok=True)

# GBK CSV
with (files / "gbk_table.csv").open("w", encoding="gbk", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["城市", "温度", "备注"])
    w.writerow(["北京", "18", "晴"])
    w.writerow(["上海", "21", "多云"])

# UTF-16 CSV
with (files / "utf16_table.csv").open("w", encoding="utf-16", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["产品", "价格"])
    w.writerow(["咖啡", "28"])

# 带 emoji 与重音字符的 CSV
with (files / "emoji_table.csv").open("w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["名称", "心情"])
    w.writerow(["拿铁😀", "开心"])
    w.writerow(["café", "très bien"])

print("site files:", sorted(p.name for p in site.iterdir()))
print("data files:", sorted(p.name for p in files.iterdir()))
