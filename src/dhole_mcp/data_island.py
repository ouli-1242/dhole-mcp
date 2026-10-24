"""`script` 肚子里的正文块（提升计划 2.2 · 数据岛）。

现代 SPA（Next.js / Nuxt / SvelteKit）常常把**完整数据**塞在
`script[type='application/json']` 或 `__NEXT_DATA__` 里——数据已经在 HTML 里，
只是不在可见 DOM 里。dhole 从前遇到这种页只有两条路：升级浏览器（3–30s）或放弃。

本模块只做**收集**，不做判决：从岛里递归捡出足够长的字符串块，跳过配置/埋点类的
键，去重、封顶。要不要拿这些块当答案，由调用侧（`server._translate_response_body`）
过同一套质量闸决定——来源是 JSON 不是免检的理由。

克制的三条来自 webclaw `data_island.rs` 的同款设计：稀疏才试（触发条件在调用侧）、
块内去重、上限 1000 块。把它读激进一分，就会把埋点配置当正文交出去——那正是
2.2 在计划里被标成"最危险的一条"的原因。
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, List

#: 一页最多收多少块（webclaw 同款上限）：一页几千块的岛是打包状态不是正文，
#: 全交出去只会把上下文撑爆。
MAX_BLOCKS = 1000

#: 一块要够长才算"内容"。标题、字段名、URL、单行提示都住在这条线以下。
#: 120 ≈ 两句中文或一句英文长句；它同时把 "..." 这类占位与 base64 短串挡在外面。
MIN_BLOCK_CHARS = 120

#: 递归深度帽：合法的内容岛没有 24 层深的嵌套，深过它的多半是打包器产物。
_MAX_DEPTH = 24

#: 配置/埋点类的键名（小写子串匹配）。命中即整棵子树跳过——配置树里偶然有一段
#: 长文案（比如 theme.description）也不该因此把配置当内容扫进来；宁可漏，不可错。
_SKIP_KEY_TOKENS = (
    "config", "settings", "env", "buildid", "theme", "locale", "i18n",
    "translation", "track", "analytic", "gtag", "gtm", "telemetr", "beacon",
    "sentry", "consent", "cookie", "advert", "experiment", "abtest",
    "featureflag", "feature_flag", "flag", "nonce", "csrf", "token", "apikey",
    "webpack", "chunk", "runtime", "hydrat", "manifest", "version",
)


class _IslandScriptParser(HTMLParser):
    """收 `script[type='application/json']` 与 `script#__NEXT_DATA__` 的原文。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.candidates: List[str] = []
        self._take: List[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag != "script" or self._take is not None:
            return
        d = dict(attrs)
        kind = (d.get("type") or "").strip().lower()
        if kind == "application/json" or d.get("id") == "__NEXT_DATA__":
            self._take = []

    def handle_data(self, data):
        if self._take is not None:
            self._take.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._take is not None:
            self.candidates.append("".join(self._take))
            self._take = None


def extract_data_islands(html: str, *, max_blocks: int = MAX_BLOCKS) -> List[str]:
    """所有数据岛里像内容的字符串块，按文档序、已去重。

    解析失败、非法 JSON、以及没有任何长块的页都交回空表——空表就是
    "这页没有可用的岛"，调用侧据此当无事发生。
    """
    parser = _IslandScriptParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return []
    blocks: List[str] = []
    seen: set[str] = set()
    for candidate in parser.candidates:
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        _collect(data, blocks, seen, max_blocks)
        if len(blocks) >= max_blocks:
            break
    return blocks


def _collect(node: Any, blocks: List[str], seen: set[str], cap: int,
             depth: int = 0) -> None:
    if depth > _MAX_DEPTH or len(blocks) >= cap:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            low = str(key).lower()
            if any(tok in low for tok in _SKIP_KEY_TOKENS):
                continue
            _collect(value, blocks, seen, cap, depth + 1)
    elif isinstance(node, list):
        for value in node:
            _collect(value, blocks, seen, cap, depth + 1)
    elif isinstance(node, str):
        text = node.strip()
        if len(text) >= MIN_BLOCK_CHARS and text not in seen:
            seen.add(text)
            blocks.append(text)


#: 给日志/回执用的一个稳定名字：岛这条路自己怎么称呼自己。
ISLAND_SOURCE = "data_island"
