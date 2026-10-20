"""搬家不是丢：每一条从常驻层搬走的说法，都必须真的能在手册里读到。

B5-17 把 connect 面从 44,016 字压到 40,010。用户划的刀是"用法细节可以离开 wire，落点
= 同一份语料两个门（`sys_guide` 与 `dhole skill install`）"，仓库自己那张
`test_wire_shape` 的挂牌写的也是同一句话：**搬家，不是缩写**。

那么这条守卫就是这个批次唯一的防伪手段。删掉的句子如果语料里没有，`sys_guide` 也
答不出来，那一次搬家就不是优化而是失传 —— 而且它不会报错，只会让下一个 agent 从零
重新发明一遍踩过的坑。所以下面这张表逐条点名"wire 上再也没有、语料里必须有"的说法，
用词边界匹配，缺一条红一条。

反证同批钉住：把语料里的那句删掉，守卫必须能看见（历史上覆盖率守卫就是这么伪装成
通过的 —— W10 那次把描述里的键名抹掉，覆盖率反而一片绿）。
"""

from __future__ import annotations

import pytest

from dhole_mcp import guide
from dhole_mcp.server import DHOLE_INSTRUCTIONS, MasterFetchServer

#: 每条 = (wire 上已删的说法, 语料里必须出现的原文片段)
MOVED_LESSONS = [
    # web_read：三档与代价
    ("archive 档要花 10-30 秒", "10-30s"),
    ("neural focus 每页 12.5 秒 CPU", "12.5s"),
    ("css_selector 只作用 HTML，别的整篇返回", "applies to HTML only"),
    ("pages 只裁提取、文件仍整篇下载", "only trims **extraction**"),
    ("check_login 要配 cache_ttl=0 才不吃缓存", "cache_ttl=0"),
    # web_read.schema：整段交给手册的那颗
    ("不写 type 只回第一条", "FIRST match"),
    ("写的 type: array 才回全部", "returns all of them"),
    ("自造关键字不起作用而不是降级", "any other JSON-Schema keyword is ignored"),
    ("重复记录必须要容器选择器", "one record per container"),
    ("每个字段最多 200 条", "Max 200 records per field"),
    # CDP：为什么第一次要人批
    ("CDP 一挂就带上那台 profile 全部登录态", "logged-in"),
    ("授权记在 cdp_hosts.json，删它即撤销", "cdp_hosts.json"),
    # web_act：动作链的形状与懒加载
    ("一个对象只能带一个动作键", "one action key per object"),
    ("snapshot 先数、按 index 打、expect 校验", "expect"),
    ("isTrusted 门控的懒加载不吃合成滚动", "isTrusted"),
    ("没有视觉模型，读图的是你自己", "no vision model"),
    # web_request：为什么 GET 被拒
    ("GET 被拒：缓存/升档/存档归 web_read", "web_read owns"),
    ("写请求只发一次、不重试", "sent once"),
    # 爬与 discover 的经济账
    ("整轮没有字符预算，只有每页 20000", "20000"),
    ("Crawl-delay 不是这里的旋钮", "Crawl-delay"),
    ("地图的大小由站点决定，预算数的是序列化后的行（C3）", "serialized rows"),
    ("地图行不按 sitemap 的文档序交回，副本折到后面且只重排不删行（C4）",
     "locale variant"),
    # C5：`timeout=` 的声明单位（整次调用的毫秒）与这一档的钳（每次尝试 30s）不是一把尺。
    # 常驻层只能放一句上限，"这颗数该怎么拿来算"整段在 fields.md 的 `timeout_applied` 行。
    ("一次 HTTP 尝试封顶 30s，抬 timeout 买的是试次不是单发", "one HTTP attempt"),
    # #42：地图那一程空手回来有两种，wire 只放得下"这颗数就是图的时钟"一句；「预算用完」
    # 与「站点答了空话」为什么是两句不同的话、以及各自下一步是什么，整段留在 tools.md。
    ("图空了要说是谁的时钟砍的：预算用完不等于站点说了不", "not a 'no' from the site"),
    # A3-a：那句 "at most 3 read-and-retry rounds" 从 wire 退场之后，完整理由（每一次
    # 调用都重新导航，所以上一轮读到的挑战这一轮已经不在了）只在语料里 —— 少了这一条，
    # 读者只知道"别重试"，不知道为什么，也就学不会"绑回会话才值得重试"那一句。
    ("验证码的轮次答的是上一枚挑战：跨调用重读等于答上一页", "already gone on the next"),
    # 刀9-7（F9）：常驻层那句 "a retry is a NEW page load, so the challenge you read last
    # call belongs to a page that is gone unless the site binds it to the session" 从描述里
    # 退场 —— 它的**病**（每次调用重导航）现在有治法（`pin=true`），而完整那一段（为什么
    # 每次导航都换一道题、驻留买回什么、TTL/换站/关页各是什么下场）一直住在 tools.md 这一节。
    # 少了这一条，读者只剩"3 次就交回人"，学不会"第 2 次之前先问页还在不在"。
    ("跨调用重试答的是上一枚挑战；驻留（pin）才是让第 2 次答第 1 次那道题的那扇门",
     "already gone on the next"),
    # 搜索
    ("consensus 数的是索引家族不是原始命中", "FAMILIES"),
    ("引擎池的上限与带 key 的引擎", "Keyed backends"),
    # 设计决策：域名声誉加权被**测掉**而不是漏掉，理由原来只活在 `_apply_quality_boost`
    # 的源码注释里（`语料不许写维护者历史`那条闸正好禁止把它搬进手册），所以语料必须自己
    # 把同一件事说成操作说法："high 不是安全判断，且这里没有黑白名单"。
    ("高 fetch_relevance 是相关性不是安全；刻意不维护域名黑白名单",
     "no domain allow-list"),
    # 状态与身份
    ("auth/cookies 必须同对象带 host", "per host"),
    ("identity_applied 只回名字和 host 范围", "identity_applied"),
    ("凭据的值不出进程", "Values never leave the process"),
    # 订阅
    ("since 传上一次最新的 published 值", "items_older_than_since"),
    ("没有日期的条目是留下并计数，不是丢", "undated"),
    # instructions：第四刀搬走的逐字段读法，落点是 topic='fields' 那一卷
    ("is_stale + content_age_days 说时效", "content_age_days"),
    ("quality_score 只属于 PDF", "quality_score"),
    ("original_url 只在重定向时出现", "original_url"),
    ("fetcher_used / escalation_path 说哪一档答的", "escalation_path"),
]


def _corpus() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))


@pytest.mark.parametrize("label,needle", MOVED_LESSONS,
                         ids=[name for name, _ in MOVED_LESSONS])
def test_a_moved_lesson_still_has_a_home(label, needle):
    """搬走的说法必须还在语料里，而且还在某一条 `sys_guide` 门后。"""
    assert needle in _corpus(), (
        f"「{label}」已经从常驻层删掉了，但手册里没有对应的说法（找过：{needle!r}）。"
        "补进 references/ 对应那一卷，否则这次搬家是失传。")


def test_the_topic_door_reaches_the_volume_that_carries_it():
    """落在文件里还不够，得是 `sys_guide` 真能送出去的那一卷 —— topic 表若指错文件，
    知识就在磁盘上而门后没有。"""
    served = set()
    for topic in guide.TOPICS:
        _head, body = guide.read(topic)
        served.add(body)
    blob = "\n".join(served)
    assert blob, "sys_guide 一个字都没送出去：topic 表与磁盘脱钩了"
    missing = [n for _l, n in MOVED_LESSONS if n not in blob]
    assert not missing, f"这些说法在磁盘上但 sys_guide 读不到: {missing}"


def test_the_guard_notices_a_deleted_lesson():
    """反证：把语料里的一句抹掉，上面的覆盖率守卫就该红 —— 它必须真的在看。"""
    corpus = _corpus()
    stripped = corpus.replace("no vision model", "", 1)
    assert "no vision model" in corpus and "no vision model" not in stripped
    assert any("no vision model" not in stripped for _l, n in MOVED_LESSONS if n == "no vision model")


def test_the_wire_no_longer_pays_for_what_moved():
    """另一半账：搬走的东西确实不在常驻层了，否则这条守卫是在给重复文本发通行证。"""
    wire = DHOLE_INSTRUCTIONS + "\n".join(
        (td["description"] or "") + "".join(
            (v or {}).get("description", "") for v in
            (td.get("inputSchema", {}).get("properties") or {}).values())
        for td in MasterFetchServer._TOOL_DEFS)
    for needle in ("12.5s", "FIRST match", "isTrusted", "cdp_hosts.json",
                   "content_age_days", "escalation_path"):
        assert needle not in wire, f"{needle!r} 还在常驻层，搬家没搬完"


def test_connect_stays_inside_the_packed_budget():
    """常驻层的总账：一次连接到底发多少字。

    尺子用 `tests/tool_payload_measure.py` 同一套算法（compact JSON，不美化），
    数字只能往下走。上限钉在实测值 +0 的位置：任何一句新描述写进常驻层都会顶红它，
    于是"要不要为这句话付每一次连接"变成一次显式决定，而不是顺手写进去的结果。
    """
    import json

    # 与 `tests/tool_payload_measure.py` 同一把尺子：MCP 发的是 `{"tools":[...]}`，
    # 那层信封的 10 个字符也算连接期开销。
    tools = json.dumps({"tools": MasterFetchServer._TOOL_DEFS}, ensure_ascii=False,
                       separators=(",", ":"))
    total = len(tools) + len(DHOLE_INSTRUCTIONS)
    assert total <= CONNECT_CEILING, (
        f"常驻层现在 {total:,} 字，上限 {CONNECT_CEILING:,}。要抬上限就是一次产品决定："
        "要么把新句子搬进 sys_guide 语料（按需付），要么说清为什么它必须每次连接都到。")


#: 实测的棘轮位。B5-17 四刀：44,016 → 40,010（instructions 3,532 → 3,070）；
#: 17.0 移除四颗状态工具：40,010 → 33,099（其中 `sys_state` 描述与那四行路由文本
#: 一起收走）；2026-10-04 G25 评审的写操作确认门：33,099 → 33,460（+361）——
#: web_request 的 confirm 属性与描述句必须住在连接期：藏在签名里的 confirm 会被
#: dispatch 当表外名拒掉，拒绝消息教的重试根本到不了闸门，所以这不是「搬去
#: sys_guide 按需付」能解决的句子，是它必须每次连接都到的显式决定（棘轮第一次
#: 上抬，理由即此）。同日 sys_state 名册补全（cdp_hosts / config 两类进册）以
#: 自身描述收紧 + ANTI_DETECT_DESC（wire 上两份）对冲，33,460 → 33,452——棘轮
#: 随实测回落。2026-10-04 G25 阶段5（M1/M2/M4/M5 四项）第一刀真往下：web_act
#: 的 anti_detect 换短指针（完整阶梯只住 web_read 一处，-83）、captcha 纪律归一
#: （墙的清单归 web_screenshot，-41）、doc_parse 扩展名清单归 file_path（-58）、
#: fetch_content 每页帽 8000→3000（响应侧，不占这页）——33,452 → 33,270。棘轮
#: 随实测回落。改它必须写理由 —— 要往上抬就是「这句话值不值每次连接」的一次
#: 显式决定，要往下砍就是一次瘦身。
#: 2026-10-05 G25 阶段6（G1，评审 High）：web_read 的 extraction_type 枚举加
#: 'check'（「这页变了吗」回执：带源站校验子回源，304 即没变，200 逐字比对给
#: 分叉窗口）。评审原文把它记在预算账内（"枚举零参数成本、wire 约＋40"），实测
#: +66（枚举 10 + 指针描述 56）——33,270 → 33,336。这是那笔账的兑现，不是新词
#: 挤占：换到的是一次几百字节的变更探测，替掉整页重取。
#: 同日阶段7（A1/A2，评审 High/Medium）：web_screenshot 并入 web_act 的截图动作
#: （四颗捕获旋钮 full_page/save_to/image_type/quality 进动作对象，纪律文案归
#: sys_guide behavior 卷）＋ web_resolve 下线（web_read 同样跟随重定向并回报落点）。
#: 13 → 11 颗，实测 -3,014 —— 33,336 → 30,322。棘轮随实测大幅回落；web_act 自己
#: +301（旋钮进描述），instructions -56（两条死路由句出、一句截图进 web_act 的）。
#: 2026-10-05 批次 3：doc_parse 补 `offset`。这不是"又想到一句文案"，是一条被实测
#: 抓到的自相矛盾：截断回执里印着 `Next offset: N`，而 doc_parse 自己**拒收** N（入参
#: 名单只有 file_path/cwd/encoding）。建议做一次调用方不许做的事，就得为那句建议付
#: 一次往返 —— 上限里买的就是"印出来的数字能原样传回去"这一件事。实测 +226，
#: 30,294 → 30,520。
#: 2026-10-05 批次 6c：12 处 wire `enum` 数组从 schema 里摘掉（实测 -319，连同把值集
#: 写进描述的重排），换成 dhole 自己的校验器（extraction_type / focus_mode /
#: focus_engine 三颗新写；force_fetcher / anti_detect / mode / freshness 四颗本来就有
#: —— 摘 enum 之前先逐颗确认这一点，否则非法值第一次落到按字面量写的内部代码）。理由：
#: 宿主那道 JSON-Schema 门拒收非法值时**不列合法值**（实测原话 "must be equal to one
#: of the allowed values"），而 dhole 的报错印出整份列表加方向（`Invalid freshness:
#: 'decade' (day|week|month|year)`）。Python 侧本来就是宽类型，枚举只让那句更有用的错误
#: 永无机会运行（房内主张见 params.py 关于 `Literal` 只是文档那段）。值集没有消失：
#: 它从数组搬进了描述散文（`'day', 'week', 'month' or 'year'`）与拒本文案两处，由
#: tests/test_the_enum_argument_values_are_checked_by_dhole.py 双向守着"每颗参数要么
#: 自己点全、要么指着点全的那一份"。
#: 三次合账：上一记实测位 30,294（web_act 的 actions 增列 hover/drag，账写在
#: tests/tool_defs_lock.json 的 comment 里），30,294 +226 -319 = 30,201，批次 5 的
#: `max_total_chars` 再 +273 → **30,474**。前两次加参数同时删了更贵的东西、净降；
#: 这一笔是这把尺子立起来之后**第一次上抬**，而抬它的理由就是它省下的那笔：没有
#: 整轮帽，默认尺寸的爬取交回 324,001 字符（实测那次 223,599），宿主把载荷截断落盘，
#: 调用方付的是整轮 token 加一次重爬。273 字每次连接，买的是"一次调用的结果读得完"
#: —— 房内规矩问的那句"这句话值不值每次连接"，这是它值的那一种。
#: 2026-10-05 批次 7：CDP 的授权对象从"调试端口主机"改成"这一次要点名去读的那个站"
#: （实测：名册 ["127.0.0.1"] 而围栏拿目标主机比对，attach 每一次都撞死）。措辞随之
#: 从 "the FIRST attach to a host" 改成 "for a site"，并把 "until it says true" 收成
#: "without it" —— 净 -7，30,474 → **30,467**。一次修 bug 顺带把话说短了，这是棘轮
#: 该往下走的那种。
#: 2026-10-05 批次 11：`web_act` 多一颗 `headed`（看得见的那扇门），+337 → **30,804**。
#: 抬它的理由是"没有这句话，能力在 wire 上根本不可达"：操作员明确否掉了批次 10 那两颗
#: env 旋钮（「多两个配置我觉得没必要，我升级到 17.0 就是避免过多配置的原因」），把
#: "什么时候需要人在场"交回给正在做事的一方 —— 而那一方唯一能传这句话的地方就是参数面。
#: 参数名与判据必须在连接期到场（表外名会被 dispatch 拒掉，藏在签名里没人调用得到），
#: 后果清单（回收豁免 / 临时目录 / 交回窗口）全部搬进 `sys_guide(topic='config')` 按需
#: 付，wire 上只留指针。第二次上抬，付的是 337 字每次连接（属性名 + 292 字描述 + 骨架），
#: 买的是一次调用就能把人要看的那扇窗叫出来。
#: 2026-10-06 批次 13：web_act / 带 actions 的 web_read 默认 60000→25000ms、
#: web_crawl deadline_ms 120000→25000ms —— 内部预算必须赶在宿主 30s MCP 超时之前
#: 返回结构化超时（实测 60s 默认被宿主裸砍成 "Tool execution timed out"，什么都
#: 回不来）；描述随默认值改口，+164 → **30,968**。
#: 2026-10-06 批次 17：连接期去重（第一刀真往下，且是无损的那一类）。同一事实在
#: INSTRUCTIONS 与工具描述里各写一遍 —— 放置规则（参数住顶层）仓库自己的决定就是
#: "只在连接期说一份"（见 server.py 里 `_INSTRUCTIONS_RULES` 上方的注释），却仍在
#: web_read / web_crawl 的描述里各留了一份副本；web_request 描述里的
#: "stored credentials ... ride along" 与它自己的 confirm 参数重复；INSTRUCTIONS 的
#: sys_guide 路由句与工具描述重复（四个触发词都在 sys_guide 描述里）。这些副本删掉
#: 不减少任何 agent 能查到的信息（两份总是在同一个载荷里一起送到）。**web_request
#: 路由句里的方法清单没有删**：它也在工具描述里，但 tests/test_web_request_b5.py 把
#: "路由的家（INSTRUCTIONS）必须点名写方法"钉成了契约 —— 37 字不值一条契约。
#: 跨工具的 timeout / ignore_robots / check_login 重复**不动** —— 每颗 schema 必须
#: 自洽，一个只读单颗工具的客户端仍要懂，删它是有损的。实测 -339 → **30,629**。
#: 这一条留在 30,968 的历史里是为了记住：上一版把这笔账记在"连接期重复"名下，
#: 而其中大部分是承重的。
#: 2026-10-06 #8 共识感知扇出：`web_search` 增一颗**可选**参数
#: `fetch_min_consensus`（int，默认 0 = 现状），只作用于 `fetch_content=true` 的自动
#: 取正文 —— 只抓被 >=N 个独立索引家族共同确认的结果，被跳过的 URL 进
#: `consensus_skipped`，每页回执带自己的 `engines_consensus`。棘轮第三次上抬，理由与
#: `headed` / `max_total_chars` 同一条：**参数名与判据必须在连接期到场**（表外名会被
#: dispatch 拒掉，藏在签名里没人调用得到）。选可选参数而非改默认行为，是因为改默认会
#: 悄悄换掉每个既有调用抓回的页面 —— 正是本项目最防的那类失败（看起来成功、结果被
#: 换了）。顶层描述里与它重复的那句按「同一事实不说两遍」删掉，净 +195 → **30,824**。
#: 判据细节（分母≤1 时不适用 / 清空候选则照实抓 0 页并报数 / 逐条回报被丢的 URL）全部
#: 搬进 `sys_guide(topic='behavior', section='web_search')` 按需付，wire 上只留一句。
#: 2026-10-06 doc_parse 补齐：同一颗工具加 `pages` / `password` 两颗参数（都只对 PDF
#: 生效），`file_path` 描述放行 `.xlsm` / `.tsv`。棘轮第四次上抬，理由仍是同一条 ——
#: **参数名与判据必须在连接期到场**：`pages` / `password` 若只活在签名里，表外名会被
#: dispatch 拒掉，agent 永远调用不到（这正是 `doc_parse` 此前"报错教你传 password、
#: 传了却 TypeError"的根因）。措辞**逐字复用 `web_read` 上同名同义字段的原文** ——
#: 同一事实一个作者，最省也最不容易漂移；首版自己另写一句（含 "Ignored for every
#: other format."）实测多付 116 字，删掉换回复用。净 +241 → **31,065**。
#: 2026-10-06 pptx 讲者备注：棘轮**往下**。备注读进来之后，工具描述里那句
#: `what a text reader cannot carry (speaker notes, layout, images)` 就不再成立 ——
#: 备注不再属于「带不走」的那一类。**不新增句子，只把 `speaker notes, ` 划掉**：
#: 第一行的职责是声明没带什么，带上了就从那张单子上下来；而读取器**总是**读备注，
#: 于是「没看到备注」无歧义地等于「这页没有备注」，再解释一遍就是同一事实说两遍。
#: 净 -15 → **31,050**（本批唯一一次向下：删掉的是一句不再为真的话，不是信息）。
#: 2026-10-07 web_act 的 destructiveHint：棘轮**往下**。SDK 规范语义 "If false,
#: the tool performs only additive updates … Default: true"，而 submit 门自己承认
#: 不可逆 —— 写 False 是主动错报，删键落回 spec 默认 true，声明从错变保守。
#: 实测 -24 → **31,026**（评审按源码字面量预估 -26，wire compact 序列化口径不同，
#: 以尺子为准）。
#: 2026-10-07 web_discover 路由行（F-A-J1）：棘轮**往下**。11 颗注册工具里唯独
#: 它在连接期没有路 —— map/crawl/read 三件事共享一行（两把键挂同一句，装配器按
#: 行文本去重，全量只发一次；任一只带其一的子集也各有路）；web_read 行删
#: `pages= for PDF ranges`（pages= 在 web_read 自己的 schema 里，不丢信息）。
#: 实测 -10 → **31,016**（评审预估 -14：crawl 行 +13 实测一致，read 行估 -27
#: 实为 -23，仍以尺子为准）。
#: 2026-10-07 三句搬手册·可拆部分（F-C1-J2 ①③）：棘轮**往下**。web_act 的
#: dict-form 枚举（手册 tools.md/examples.md 逐项承重，wire 换 sys_guide 指针）
#: 实测 -193；doc_parse 的 charset 恢复 how-to（运行时错误＋fields.md 的
#: metadata.encoding 行已承重，wire 留探测契约＋fields 指针）实测 -33。
#: 合计 -226 → **30,790**。②web_crawl 的 Caps 句与 P11 回收互斥（同一句只计
#: 一次），本批不动，留 P11 定夺。
#: 2026-10-07 P11 三阶段断点续传（F-B-2，批次8 刀2）：棘轮**往下**。web_discover
#: 收尾把 URL 清单存成 plan_id，web_crawl 增 resume_token/selector/schema 三颗
#: 参数 —— 翻页从「agent 当传送带」（每批 re-emit 全部参数）变成 O(1) 断点续传
#: （token 逐字带回）。三参骨架实测 +318 → 峰值 31,108，撞棘轮；抵扣来自
#: web_crawl 描述重写：url= 路由句删除（resume_token 把路由事实说得更准）、
#: Caps 句在此**一次性 claim**（互斥约定的兑现，不再两边各付一次）、deadline_ms
#: 与 robots 两句压缩。回收 +340，净 **-22 → 30,768**。
#: 2026-10-07 评审R2刀4（P2-7，本计划唯一动 wire 的那刀）：棘轮**往下**。
#: web_search 的 `query` 从 wire 的 required 下来了 —— mode='find_similar' 的输入是一颗
#: URL，宿主按 required:['query'] 先把这种调用拦下，那颗模式因此在正常宿主上做不到
#: （不传被拒、硬传又被服务端整个忽略）。判决搬进服务端按 mode 判，wire 不再拦。
#: 净额按实测：required 撤下(-21)、query 描述改说『除非 find_similar 可选』(+47)、
#: url 描述瘦字(-24，同一事实的第二个作者)、min_relevance 那句瘦字(-21)。**净 -18 →
#: 30,750**（mode 那句『'find_similar' needs url=』必须留着 ——
#: test_the_enum_values_are_still_stated_where_a_reader_looks 要求一颗参数的描述自己
#: 点得出全部合法值，摘掉它是一次能力失传，不是瘦身）。棘轮随实测回落。
#: 2026-10-08 验收修复刀（D-07 + D-01，同一句账单）：棘轮**再往下**。
#: web_act 的 submit 那句写的是 `(nothing is filled or sent)`，实测同一条链里 submit
#: 之前的 `fill` **已经落在页面上** —— 这不是措辞问题而是常驻层里一句假话：调用方照它
#: 决定要不要整条链重跑一次。改成的那句多 12 字。抵项来自同一颗工具的凭据句：
#: `session_id` 与 `cdp_url` 各自的参数描述早就写着 jar 的名字 / 挂上用户自己的
#: Chrome，DHOLE_INSTRUCTIONS 也写着凭据进 cred_defaults —— 同一件事第三次说，摘掉。
#: **净 -43 → 30,707**。回落后的余量不许预支：上限仍钉在实测值 +0。
#: 2026-10-09 独立审计修复刀（D1 + D4，同一句账单）：棘轮**再往下**。
#: 两条都是常驻层里的**假话**，不是措辞问题：
#:   D1 `web_act.confirm` 曾写「Two things need it ... Neither acts until this says
#:   true」，实测顶层 confirm **不能**放行 submit（放行写在动作对象内，四条对照
#:   组合的订单日志 delta 为 0/0/1/1）。读者照它做会在「暂停→加顶层 confirm→
#:   仍暂停」之间循环，而写闸是安全控制 —— 这是必须每次连接都到的一句。
#:   D4 `cred_defaults.set` 曾把 `host` 说成 auth / cookies 内部的一颗，实测取的是
#:   兄弟键（`set={"host":..., "auth":{...}}`），五种「host 写进 auth 里」的形状
#:   全被拒。改说 BESIDE，同时删掉「the last two only with ... in the same object」
#:   那段既误导又长的从句。
#: 逐字符实测（wire 口径含 JSON 转义）：D1 158→192(+34)、D4 190→150(-40)。
#: **净 -6 → 30,701**。auth 的三形态不进这里：references/tools.md 已逐字写着，
#: 按需取段 —— 常驻层替说明书干活正是本文件要防的形状。
#: 2026-10-10 体验修复刀3-C2：棘轮**上抬 +18**，因为常驻层里有两句假话。
#: `web_crawl.search` / `web_discover.search` 都写着 "(URL path + page title)"，
#: 而 `sitemap=true` 的图只有站点自己发布的 `loc` —— 没有标题可比。C2 把那个参数
#: 从"在帽之后筛"（在 sitemap 分支上等于根本没筛）推进 `accept`，响应侧从此自报
#: 少掉的那一半；描述侧只需要不再承诺标题那一半：
#: crawl 61→65(+4)、discover 50→64(+14)。**+18 → 30,719**。
#: 刻意没有把"sitemap 分支只比 URL、且筛在帽之前"整段搬进常驻层 —— 那句话已经逐字
#: 在 references/tools.md 里，按需付；这里只付"闭嘴于说不准的那半句"的钱。
#: 2026-10-10 体验修复刀3-C3：棘轮**上抬 +489**，买的是"地图的大小从此有主人"。
#: 一张地图的行数由站点决定，而 web_discover 这扇门上原来**两颗旋钮都没有**：
#: `max_total_chars` 只在 web_crawl 上（web_discover 转发时不传，永远吃默认的
#: 60,000），而游标 `resume_token` 只通向正文。C3 让预算第一次量到地图行（地图行
#: 的 content 恒为空，所以那道闸从前对地图从不应战），代价是一刀切掉默认形状的
#: 一部分 —— 被切掉的行必须有路可拿，否则就是新的静默截断，于是 `resume_token`
#: 也上这张门（读的是已存下的地图，不再走网络）。逐字符实测：两颗参数行 +332、
#: 描述尾句 +157。30,719 → **31,208**。
#: 没有把"游标页只带 URL、lastmod 不在计划里"写进常驻层：那句话在 summary 里逐次
#: 出口，而字段口径归 references/fields.md。
#: 2026-10-10 体验修复刀3-C5：棘轮**上抬 +215**，买的是 `timeout=` 不再一句说两种单位。
#: 它声明的是**整次调用**的毫秒（"max 120000"），而 HTTP 一档钳的是**每次尝试**的 30s
#: （`bulk_get` 的算式），一份文档又只有一次尝试（重试把下载从第 0 字节重启）—— 所以
#: 超过 30s 之后是一句够不着的承诺，而 `_document_budget_advice` 还在原地劝人抬它。
#: 按退掉一颗工具省多少的同一把尺实测：web_read +98、web_crawl +66、web_request +52、
#: feed_read −1（那颗的默认值印的是 30000 而代码用的是 20s）。feed_read 那一处顺带修掉
#: 一个真单位错：它的 `timeout` 原样下传给按秒算的 `HTTPSession`，`timeout=30000` 因此
#: 是 30000 秒。收据本身（`timeout_applied`）与它的算术都在 references/fields.md，
#: 不占这一页；上限为什么留在那一处不改（抬它是一次抓取行为的决定）写在
#: tests/test_a_timeout_bigger_than_the_per_attempt_cap_says_what_it_bought.py 的头。
#: 2026-10-10 体验修复刀3-A2：棘轮**上抬 +28**（31,423 → 31,451），买的是"必填载荷键第一次
#: 上 wire"。`actions` 那句从前数完 11 个动作名就只说"each targets a CSS selector or a
#: snapshot index"，一个载荷键都没点名 —— 于是 `text` 与 `key` 在任何 wire 文本里都不存在，
#: 只读 `tools/list` 的调用方只能猜（实测猜错两次：`{"fill": {..., "value": ...}}`、把 press
#: 的键也写成 text），每次一句报错加一次重跑；答案一直住在报错里而不在声明里，而那正是
#: "先失败一次才知道怎么写"的定义。
#: 第一版把整表（含 `count/state/timeout_ms/steps/ms_per_step/full_page/save_to/expect?/
#: confirm?`）都搬上常驻层，实测 519 字符，撞了 `tests/test_wire_shape.py` 那颗 296 的闸。
#: 那道闸 prescribed 的修法从来不是抬闸，是**搬家**：可选旋钮留在 references/tools.md 的
#: Page interaction（那一节本来就是全的，缺的从来不是知识，是它到不了第一次调用眼前），
#: wire 只带三颗**必填**键 —— 少带一颗就退回原来那桩错，多带一颗就是白付。
#: 名单与 `actions._VALID_KEYS` 相等；三颗必填键由验证器自己的报错推出来（不手抄）并要求
#: 声明里同句连读（`fill needs text`）。钉住这两件事的是
#: tests/test_an_agent_that_only_reads_the_wire_can_name_an_actions_payload.py。
#: 2026-10-10 体验修复刀3-A3（a 文案 + b 链内 read，同一刀）：棘轮**上抬 +373**
#: （31,451 → 31,824），抬它的是常驻层里那句做不到的承诺："Captchas get at most 3
#: read-and-retry rounds"。每一次调用都重新导航，所以上一次读到的挑战属于已经不在了的那个
#: 页面实例 —— 第二轮填进去的是上一轮那一枚的答案（实测靶站 `/captcha` 每次 GET 出新
#: token：提交 `72G69` 而站点那枚是 `CJN2Y`）。同一句假话还有第二个作者：VISUAL ASSIST
#: 那条写着截图是 "the way to read a CAPTCHA you are about to type"，而那张图要等整条链
#: 跑完才交得到调用方眼前 —— 链里没有一步能照着它打字，顺序被说反了。
#: 改完的两半都是结构事实：轮次只在站点把挑战绑回会话时才有意义；图像挑战一律转交；而页面
#: **把挑战写成文本**的那一种，同一条链里读得到也用得上（A3-b 的 `{"read": {...,"as":
#: "code"}}` + `{"fill": {"text": "{=code}"}}`）。这一对必须住在 wire 上：只读 `tools/list`
#: 的调用方此前唯一的出路是再调一次，而那一次正好换掉挑战。
#: 逐块实测：动作清单 +6（一颗动作名），工具描述 +367（撤一句做不到的、给一对用得上的）。
#: `{=名字}` 的完整旋钮表与"为什么跨调用不行"整段在 references/tools.md 的 Page interaction
#: —— 参数描述那颗 296 的闸装不下第二份（A2 刚试过），所以形状给在工具描述那一份。
#: 2026-10-10 体验修复刀3-W1：棘轮**上抬 +642**（31,824 → 32,466），买的是这一批里
#: 唯一一句"调用方对自己那一发请求没有任何说法"。缺陷的原文是人的话："我没设过的头"。
#: 实测一次零点名的 `web_read` 往站点发 **15** 颗头：一枚编造的 `referer:
#: https://www.google.com/`、browserforge 生成的 UA 与 `sec-ch-ua*` 一家，外加
#: `sec-fetch-site: "?1"` —— 那是 `Sec-Fetch-User` 的值，真 Chrome 在 `Sec-Fetch-Site`
#: 这一位发 none / same-origin / cross-site。16 发抽样里 13 发带着它，3 发把手机 UA
#: 配在 `sec-ch-ua-platform: "Linux"` 上。而旧参数面上没有任何一颗能改它：
#: `extra_headers` 是 §3-17 具名拒绝的旧拼写（它当年搬走的是**身份**，不是话），
#: `referer` 硬编在两个调用点上（`stealthy_headers=True` 那两颗字面量）。
#: 这一刀做三件事，其中两件动 wire：① `headers=` 上 web_read（+386，含三族拒绝各自
#: 那扇门的地址）与 web_request（+256）；② 那两颗字面量交给 `anti_detect` 档位表读
#: （公开面**没有**多第二颗伪装旋钮：`off` 才是"只发我点名的"那句话的落点，实测剩
#: 14 颗、无 referer，`summary` 在真跑过 HTTP 层的那一发上说这句话）；③ 库层的不自洽
#: 写成明说的限制（references/troubleshooting.md），连同一条**自我更正**：计划里曾把
#: `sec-purpose` 与 `upgrade-insecure-requests` 记成"primp 编译层之下、dhole 的字典
#: 覆盖不掉"，实测**覆盖得掉** —— primp 只补字典里没有的名字。所以拒绝名单上没有
#: "技术上做不到"这一类，只有三条设计边界（凭据 / UA / 校验子），每条都点名自己的门。
#: §3-17 那条边界一个字没动：`auth` / `cookies` / `proxy` / `extra_headers` /
#: `allow_private` 五个旧拼写仍以名字被拒，`headers=` 里点名 cookie/authorization 也一样拒。
#: 2026-10-10 体验修复刀3-D2：棘轮**上抬 +30**（32,466 → 32,496），买的是常驻层不再对
#: 一台三个月前批过的主机说"这是第一次"。名册从前是无时间戳的裸 host 列表 —— 三月的按键
#: 到十月还在放行，而这台机器上那两条残留批准证明的是**撤销不可发现**，不是 TTL 多余。
#: 现在磁盘形状是 `{host: 批准的 ISO 时间戳}`，过窗（30 天）重新征询而不是静默失效；旧形状
#: 读成"无日期"，同样要再按一次，不替用户补盖他没按过的戳。三处描述改了词（confirm +24 /
#: web_read.cdp_url +4 / web_act.cdp_url +2），三处成因各说各话（从未批过 / 过窗并引用日期 /
#: 无日期可读），`sys_state` 的 census 与撤销回执一起点名每台日期、年龄、名册路径与撤销命令。
#: 连带修的一处漂移：`dhole doctor` 的 cdp fence 行从前自己抄一遍名册算术，会把过窗的算成
#: "已授权"，与闸门说的正好相反 —— 现在它读 server 的那一份，判活只有一个属主。
#: 2026-10-10 体验修复刀4-#42：棘轮**上抬 +211**（32,496 → 32,707），买的是 `web_discover`
#: 那颗 `timeout=` 第一次有它自己说的时钟。常驻层从前写 "Whole-call budget"，而地图那一程跑在
#: 引擎自己的 `deadline_ms`（25000）上、那一发索引请求又写死 15 秒：实测一台 20s 才答的站点，
#: `timeout=30000` 与 `timeout=120000` 交出同一次 30.0s、同一张空地图、同一句怪站点的话
#: （"fetching or parsing it produced nothing"）。现在那颗数同时是整发的 deadline 与那一发请求的
#: 时钟，越界具名拒绝，而被时钟砍掉与站点答了空话分成两句话 —— 完整的一把尺留在 tools.md，
#: wire 只付这一颗参数的 +211。
#: 2026-10-11 期4 第六刀：棘轮**随实测回落 -7**（32,707 → 32,700，余量仍 0）。`web_search`
#: 的 `before=` 拒辞换成逐行判决的说法，而这一句还修掉一个方向错误：第一版按行丢的是"比
#: 截止更早的行"，而 `before=` 是一轮的**上界**（与 `after=` 成对，也是所有搜索 API 里
#: `before:` 的意思）—— 写反的那一版丢掉的恰好是调用方要的那批，而回包字面上仍然像"过滤过
#: 了"（有计数、有 summary、`exact:false` 也写着）。旧句 "REFUSED, not ignored: … results
#: carry no publish date" 在 4.1-4.3 之后已不是真话（sogou/baidu/bing 的容器实测带着日期），
#: 新句 152 字把"这是上界""逐行做的""丢掉的是证明更晚的""判不了的留下并计入 date_coverage"
#: 四件事一起说完，替代路径的指针搬进响应侧的 `date_filter.note`（那一发真问过日期才送到，
#: 比每次连接都付一份便宜，也更准）。搬走的理由与钉子都在
#: `tests/test_search_date_window_g24.py::TestTheWireText`。
#: 2026-10-11 验收刀9-7（F9）：棘轮**上抬 +128**（32,700 → 32,828），买的是 `web_act` 的
#: 新公开参数 `pin=true` —— 页面驻留。这一颗必须住在连接期而不是 sys_guide 那一卷：参数
#: 不在 tools/list 上就根本说不出来（dispatch 按表外名拒掉，而"读到的那道题不是正在答的
#: 那道题"是体验报告里唯一一条**骂人级且机械性必错**的摩擦：靶站每次 GET 换一枚 token，
#: 跨调用重试在结构上不可能答对，人第二次 403 之后就直接放弃了）。付法逐笔：新属性的
#: JSON 成本 +215（描述 174 字 + 那颗键的壳），同一段里从前那句
#: "a retry is a NEW page load, so the challenge you read last call belongs to a page that
#: is gone unless the site binds it to the session"（138 字）整段退场，换成 50 字的指针
#: （"each call is a fresh page unless pin=true keeps it"），−88；完整理由一直住在
#: tools.md 的 Page interaction 一节，MOVED_LESSONS 新加的那一对把它钉在语料里。
#: 净 +128 = 常驻层的 0.39%，买一次结构性必错的修法。
#: 2026-10-15 R02-F34（R02-054）：棘轮**上抬 +260**（32,828 → 33,088）。买的是「这台机器的
#: 语义重排器装了没有」第一次在 wire 上点名问得出：`show` 的枚举多一颗 `models`，sys_state
#: 的描述多一行路由（这一类存在、且它是只读的 —— dhole 不抹模型文件）。必须每次连接都到的
#: 理由与 G25 那次 confirm 门同类：宿主在**调用之前**就得决定要不要问这一格，而只住在
#: sys_guide 语料里的名字，调用方不知道可以问，也就永远不会问。四格 `state` 怎么读、
#: 截断下载为什么算「不在」——这些解释一律留在 tools.md，按需付。
# 2026-10-20：sys_guide topic 描述 296→285（start/manual 拆分），总量 33,088→33,077。
# 棘轮跟着实测往下收，不留在旧数上。
# 2026-10-20 P0 纵向搜索：web_search 增 `sources`（web|news|academic|qa|code|community）
# 与 `sort`（relevance|newest）两颗可选参数。棘轮**上抬 +133**（33,077 → 33,210）：
# 参数名与值集必须住在连接期（表外名会被 dispatch 拒掉；enum 数组按批次 6c 的裁决
# 不进 wire，值集改由描述与 dhole 自己的拒绝文案点全）。买的是"一次调用就能问
# 新闻/论文/问答/代码/社区"这条新搜索维度 —— 五个后端（GDELT/arXiv/Stack Exchange/
# GitHub/Hacker News）的细节、限速与日期窗语义全在
# sys_guide(topic='behavior', section='web_search') 按需付；wire 上只留参数面、
# 一条行为句，以及"纵向行不参与 engines_consensus"这句读响应时必须知道的语义。
# 首版那两张带 enum 的完整描述实测 +860；按"同一事实不说两遍"收短旧描述
# （min_relevance/min_raw_relevance 两颗参数自己的描述已经说清两把尺子）并摘掉
# enum 数组后压到 +133。
CONNECT_CEILING = 33_210


def test_the_price_table_in_the_manual_is_the_current_one():
    """配置那一卷里的价目表必须等于尺子此刻的读数。

    这卷是 `sys_guide(topic='config')` 送出去的：操作员照它决定要不要裁注册面。
    一个过期的"总开销"不会报错，只会让人按错的价做决定 —— 所以这里只认逐字符相等，
    而且连价目表里那颗最贵的（`web_read`）一起钉住：它变了而文档没变，就是文档在说谎。

    D-33（2026-10-09 真机 stdio 量）：这里的 `wire()` 曾经两侧都加**全量**
    `DHOLE_INSTRUCTIONS`，于是算出的"单价"只含 tools/list 那一行，而注释和手册都写着
    它是"总开销增量"。真机减掉 web_read 少付的是 6,061（5,937 行 + 它自己那句 124 字
    路由），不是 5,937。改成 `_compose_instructions(sel)` —— 也就是 wire 实际发的
    instructions —— 单价与"总开销"从此共用一把尺。
    """
    import json

    from dhole_mcp.server import MasterFetchServer, _compose_instructions

    defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}

    def wire(sel):
        return len(json.dumps({"tools": [defs[n] for n in sel]}, ensure_ascii=False,
                              separators=(",", ":"))) + len(_compose_instructions(frozenset(sel)))

    allnames = list(defs)
    total = wire(allnames)
    # 单价的口径写死在这里：从全量注册面里退掉这颗工具省下的**连接期总开销**
    # （tools/list 那一行 + 它前面那个逗号 + 它自己的路由行；共用一句路由的工具
    # 各退各的省不到 instructions）。手册里的价目表按同一个定义写，否则同一颗工具
    # 会有三个"成本"。
    #
    # 钉全部 11 颗而不是最贵的一颗：这张表是操作员唯一的单价来源，而"哪些工具共用
    # 路由行"恰是最容易写错的一列（web_crawl / web_discover 各退一次，instructions
    # 一字不动 —— 真机量过）。
    cfg = (guide._ROOT / "references" / "configuration.md").read_text(encoding="utf-8")
    assert f"**{total:,} chars**" in cfg, (
        f"配置手册写的连接期总开销不是实测值（现在量到 {total:,}）—— 改文档，"
        "或说明为什么这次改动不该进 wire")
    stale = []
    for name in allnames:
        price = total - wire([n for n in allnames if n != name])
        if f"`{name}` {price:,}" not in cfg:
            stale.append(f"{name}: 手册价 {price:,} 不在表里")
    assert not stale, "价目表里有颗单价过期了（按退掉它省多少的口径实测）：\n" + "\n".join(stale)
