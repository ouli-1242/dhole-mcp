# fix-R01-F2 · R01-003 resume_token 自荐形状缺 url

- **现象**：`web_crawl` 页帽停下后 next_action 教「pass resume_token='…' to web_crawl」，照抄（不带 url）→ `Missing required argument(s) for web_crawl: ['url']`（P02 老周 8 秒白烧）。
- **根因**：crawl.py `_resume_sentence` 的句子少写 url；同文件地图路的两个变体（web_discover 文案）都带着 `web_discover(url=…, resume_token=…)`。门要求 url（resume_token 只认队列），自荐句就必须带。
- **修法**：`_resume_sentence(token, held, kept, url)` 新增 url 参，句子改为 `web_crawl(url='…', resume_token='…')`；两处调用点（字符预算停 / 页帽与时限停）传 `url`。语义两句话（不重抓、held 数）逐字保留。
- **测试**：`tests/test_a_resume_recommendation_carries_the_url_the_gate_demands.py`——句含 `web_crawl(url=`、token 原样、「without re-fetching the N page(s)」照旧。
- **影响面**：crawl.py 一函数 + 两调用点。
- **副作用**：无（纯文案；地图路文案本就带 url，两路从此一致）。
