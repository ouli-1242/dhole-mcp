# round-11 计划（P0 封存）· 主题：冷面深水（doc_parse · web_discover · web_request · feed_read）

> 分支 review/round-11 · 基线 2ab7b5e（round-10 收官 + hygiene）· 服务 127.0.0.1:9211 + 靶站 8399。
> 主攻**十轮里被打得最少的四颗**：doc_parse（累计 ~5 发）、web_discover（~6 发）、
> web_request（~4 发）、feed_read（多源/since/编码面）——每颗由专门画像深打，外加
> 「第一拒全集抽样」做粘合线。P0 预检：仓库无 PDF 样例 → doc_parse 用**本地构造文件**
> （CSV/GBK/伪 PDF），discover/discover 地图对 /collections 实测 12 URL。
> 回归压力：round-10 必测回归点 + 各轮家产。

## 十张画像卡（封存，执行前不改）

### P01 桑无咎5 · 字幕组长 L3（猎手）
- 目标：doc_parse 深水：构造 UTF-8 CSV / GBK CSV（R01 家产）/ 伪 PDF（假头字节）→ 编码声明、not_a_pdf 具名、多 sheet 小计（E 观察在册——看是否仍缺）。
- 验收：每种坏形都有具名答复；GBK 不乱码。
- 主攻：doc_parse + sys_guide。

### P02 白景舟3 · 珠宝鉴定师 L3（猎手）
- 目标：web_discover 深水：/collections 地图完整性（12 URL vs 页面实链数）、断链页的行账、depth 参数语义、discover→read 的行可点性。
- 验收：地图不漏不编；断链如实。
- 主攻：web_discover + web_read。

### P03 阮鸿飞5 · 地产经纪 L1
- 目标：web_request POST 流（/login 表单）→ 写闸语义（method 门）→ session 配合 → /account 复查。
- 验收：写闸具名；POST 成功账目；会话保持。
- 主攻：web_request + web_read。

### P04 云苏5 · 农技员 L1
- 目标：feed_read 增量：since= 边界（含无日期条目计数家产）、max_items 与 note 的全量/在途账。
- 验收：账目逐发可读（R01 家产）。
- 主攻：feed_read + sys_guide(start)。

### P05 越千帆5 · 播客制作 L2
- 目标：feed_read 多源聚合：靶站 feed + 一个坏 URL（404）+ 一个私网坏地址混发——逐行分账形状。
- 验收：坏行不拖垮好行；逐源 error 具名。
- 主攻：feed_read（混发）。

### P06 黎照4 · 气象员 L2
- 目标：web_request 深水：redirect 端点账、include_headers 的头披露（R05 家产）、content_type 自定义 body。
- 验收：重定向链如实；头不泄 Cookie 值（家产）。
- 主攻：web_request。

### P07 甘棠5 · 飞行签派员 L3（猎手）
- 目标：第一拒全集抽样：13 颗各发一颗形状错（错误参数名/类型）——第一拒是否颗颗带修法句/合法名单；「invalid_request: 前缀双形状」契约回归（R09 教训在册）。
- 验收：颗颗教出路；无裸崩。
- 主攻：13 颗参数面 + sys_state。

### P08 屠维5 · 电竞教练 L2
- 目标：discover→crawl→read 地图流水线：discover 的行作 crawl 种子，地图与抓取行数对账。
- 验收：行数不串账；断点续爬 resume 家产保持。
- 主攻：web_discover + web_crawl。

### P09 库尔班5 · 营养师 L1
- 目标：照手册走 read 路线（focus/css_selector 组合），错误现场按句自救。
- 验收：L1 不迷路；css_selector 家产（0 匹配说出去）保持。
- 主攻：web_read + sys_guide。

### P10 越千帆5b · 手语翻译 L2
- 目标：doc_parse 边界：不存在路径/目录/超大行数截断声明；feed_read 空源与 limit 边界（R09-002 家产：limit 具名 clamp 应同款在场）。
- 验收：边界全具名；clamp 家产跨工具一致。
- 主攻：doc_parse/feed_read 边界。

## 覆盖核对

- 职业 10 全新（字幕组长/珠宝鉴定师/地产经纪/农技员/播客制作/气象员/飞行签派员/电竞教练/营养师/手语翻译）；L3×3（P01/P02/P07）、L1×3（P03/P04/P09）✓。
- 13/13 目标：冷面四颗主攻 + read/search/recall/research/crawl/act/state/guide/cred 旁支编织 ✓。
- 回归绑定：round-10 必测 6 项 + R09-002 clamp 家产（feed limit）+ R01 GBK/在家产 + 各轮家产。

## 执行参数

- 节流：画像间 ≥18s；场景间 5-8s。
- 端口：9211；env DHOLE_IGNORE_ROBOTS=1（注明不可观测）。
- 方法论：先引手册/描述原句 → 真机回放 → 记「合/不合」；判定前读全四账面字段。
