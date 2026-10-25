# fix-R03-F1 · R03-004 page_type 字段描述点名「抽取形状 ≠ 页面体裁」

- **现象**：P10（L1）读 B 站视频频道索引页拿到 `page_type: "article"`，把它读成「这页是文章」——实际契约是「散文抽取成功」（trafilatura 找到 ≥200 字主内容、link_density<0.5 → article，crawl.py `_classify`）。抽取没错；错在字段描述只说 "How the page was extracted… what kind of content it got"，没把 article 的语义边界说出口。
- **根因**：词表（article/list/js_shell/fallback）的语义是**抽取形状**，描述却没有声明这一层，读者（尤其 L1）按页面体裁理解。
- **修法（描述层）**：crawl.py `CrawlPage.page_type` 描述改为点名——"How the page was **EXTRACTED** - the shape of what came back, not the page's real genre: article = prose was extracted (any page whose main content reads as prose: docs, posts, but also a channel page with a blurb), list = …, js_shell = …, fallback = … Read it before trusting `content`, not as a claim about what kind of site this is."。词表与 `_classify` 判定逻辑一字不动。
- **测试（先红后绿）**：`tests/test_a_page_type_word_describes_the_extraction_not_the_page.py`——断言描述含 "prose"（article=散文抽到了）与体裁免责（genre / not the page）。修前红（NameError 修正后红在断言），修后绿。
- **影响面**：响应模型字段描述；**wire 价格不受影响**（output model 不进 tools/list 的 inputSchema——test_wire_moves/test_server_3/test_fetcher_1 在改动后全过）。
- **线上复验**：真重启后 crawl 重放：/collections → article（269B 散文，语义正确）、/work/1 → fallback；page_type 取值行为零变化（sessions/verify-r3.log）。
