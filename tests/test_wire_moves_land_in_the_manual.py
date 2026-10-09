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
CONNECT_CEILING = 31_208


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
