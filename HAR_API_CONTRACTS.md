# HAR 接口契约与投注数据映射（基于本轮上传文件）

> 仅记录抓包中实际出现的接口与字段。Cookie、Token、cuid、deviceImei、动态签名和订单号不写入本文或仓库。

## 1. 文件识别与边界

- `pingbo-2(4)`：2,411 条 HAR entry。2,402 条请求指向 `www.part567.com`，识别为 Pingbo/Part567 抓包。
- `op (1)(1).har`：332 条 HAR entry。主要 API 主机为 `api.dbsportxxx278gwf4.com`（146 条）及 `api.live336.com`（9 条），OP 前端主机为 `op7-spa-testing.test800.xyz`。
- 本轮上传文件中没有独立的 FB API 抓包主机 `api.738z3.com`；该域名仅作为仓库既有 FB 配置保留。
- `api.dbsportxxx278gwf4.com` 的 `matchesPagePB`、盘口详情和 `yewu13/.../bet` 请求构成同一条赛事/盘口/投注链路；仓库按既有项目命名将该链路归类为 EB。`api.live336.com` 的 `gameType=OB` 列表返回同一 `matchId`，作为赛事 ID 对照接口记录。

## 2. Pingbo 赛事列表与内嵌盘口

### 主接口

- 方法：GET
- URL：`https://www.part567.com/sports-service/sv/compact/events`
- 本次抓包：18 次。
- 同一主机上还观察到 `/sports-service/sv/compact/carousels`（670 次）和 `/sports-service/sv/compact/sports-markets`（136 次）；它们是轮播/运动盘口菜单，不应代替主赛事列表。

### 请求参数（抓包值）

`btg=1, cl=3, g=QQ==, hle=false, ic=false, ice=false, inl=false, l=3, lv=<动态版本>, me=0, mk=1|2, more=false, o=1, ot=1, pa=0, pimo=0,1,8,39,2,3,6,7,4,5, pn=-1, pv=1, sp=29, tm=0, v=<动态版本>, locale=zh_CN, _=<时间戳>, withCredentials=true`

- `lv`、`v` 是增量刷新版本值；首轮请求为 `0`，后续请求会回传上次响应的版本时间。
- `mk` 在抓包中出现 `1` 和 `2` 两种值；不可把某一次请求值固化为唯一盘口状态。
- 本次 `sp=29`，响应运动名为 Soccer。它是本次抓包的足球样本，不代表篮球查询参数；切换运动类型应从页面实际请求或运动菜单确认。
- `_` 为缓存规避时间戳，`withCredentials` 是请求层参数。

### 响应与解析

响应顶层字段观察为 `u,l,n,e,e1,pt,ps,d,hle,ce,be,ces`。

- 首次/全量列表主要位于 `l`；增量刷新时可能返回 `l=null`，而把比分/状态变化放在 `u`，因此解析器必须同时处理全量列表与增量更新。
- 赛事 tuple 的已观察结构为 `[eventId, homeName, awayName, status, startTimeMs, ... , markets]`；当前解析器以第 0、1、2、3、4、8 位提取赛事 ID、主客队、原始状态、开赛时间和盘口对象。
- 盘口对象按 key 分组。当前解析逻辑仅把 key `0` 的数组按让球候选解析、key `1` 的数组按大小候选解析；其余数字索引没有足够证据，不做语义推断。
- 所有原始 tuple 索引应继续保留，避免将未证实的字段解释写死。

## 3. EB 赛事列表与盘口接口

### 赛事列表

- 方法/URL：`POST https://api.dbsportxxx278gwf4.com/yewu11/v1/m/matchesPagePB`
- 本次抓包：8 次。
- 请求 JSON 字段：`type, sort, device, euid, cuid, cpn, cps`。
- 抓包样本值：`type=1, sort=1, device=v2_h5, cpn=1, cps=50`；`euid` 与 `cuid` 属于会话/用户上下文，必须在本地运行时提供，不应从 HAR 复制到配置或提交。
- 响应外层：`{code, data, msg, ts}`。`data` 是 Base64 + gzip 压缩内容；解压后为 `{pages, data: [event...], cto}`。
- 本次每页 `cps=50`，响应 `pages=2`。后续翻页应递增 `cpn`，不能只固定读取第一页。

### 赛事对象关键字段

| 字段 | 含义/用途 |
|---|---|
| `mid` | 赛事 ID，投注链路中的 `matchId/matchInfoId` |
| `mhn` / `man` | 主队 / 客队 |
| `tn` / `tnjc` | 联赛全称 / 简称；本次投注请求的 `matchName` 对应全称 `tn` |
| `tid` | 联赛/赛事类别 ID，投注请求映射为 `tournamentId` |
| `csid` / `csna` | 运动 ID / 运动名称，映射为 `sportId / sportName` |
| `mgt` | 开赛时间毫秒值 |
| `mst` / `mmp` / `mms` / `mo` | 原始比赛状态/阶段字段；不能仅凭一个字段推断可投注状态 |
| `hps[]` | 玩法列表 |
| `hps[].hpid / hpn` | 玩法 ID / 玩法名称 |
| `hps[].hl[].hid / hv / hn` | 盘口 ID / 盘口线 / 序号候选 |
| `hl[].ol[].oid / ot / ov / onb` | 选项 ID / 选项类型 / 原始赔率整数 / 选项文本 |

### 单场盘口赔率详情

- 方法/URL：`GET /yewu11/v1/m/matchDetail/getMatchOddsInfoPB?mid=<matchId>&mcid=0&cuid=<session>`
- 本次抓包：42 次。
- 响应外层同样为 `{code,data,msg,ts}`，`data` 为 Base64 + gzip，解压后是玩法数组。
- 详情中的 `hpid/hpn`、`hl[].hid/hv`、`ol[].oid/ot/ov/otv` 是构造盘口选择的核心字段。
- 赔率整数按本次接口数据口径除以 `100000` 得到十进制赔率。投注前仍必须调用最新盘口校验接口，不能直接使用赛事列表或详情抓包时的旧赔率。

## 4. OP 赛事列表到投注接口的组装链

本次 `api.dbsportxxx278gwf4.com` 抓包中观察到 4 次 `bet` 请求，以及对应的最新盘口和限额校验请求。推荐的组装顺序如下：

1. `matchesPagePB` 获取赛事对象及其 `hps -> hl -> ol` 盘口树。
2. 用户选择某个选项后，调用 `getMatchOddsInfoPB`，按 `mid` 获取单场盘口详情，确认 `hpid, hid, oid, ot, ov, otv`。
3. 投注前调用 `queryLatestMarketInfo`（POST）。请求 `idList[]` 的字段为 `matchInfoId, marketId, sportId, oddsId, oddsType, playId, placeNum, matchType`，并带运行时 `cuid`。响应中的 `marketOddsList[].oddsValue` 是最新赔率值；同时检查 `oddsStatus, matchStatus, matchOver, pendingOrderStatus`。
4. 调用 `queryMarketMaxMinBetMoney`（POST）获取最小限额等信息。请求 `orderMaxBetMoney[]` 包含 `deviceType, oddsFinally, oddsValue, playId, playOptionId, matchId, marketId, matchType, openMiltSingle`；响应包含 `minBet, orderMaxPay, globalId`。抓包中的 `orderMaxPay` 是最大派彩金额字段，不应误当成最大投注额。
5. 只有最新赔率仍可用、比赛/盘口状态通过校验且金额不低于 `minBet` 时，才组装 `/yewu13/v1/betOrder/client/bet`。

### bet 请求字段映射

| 投注字段 | 来源 |
|---|---|
| `matchId` | 赛事 `mid` |
| `tournamentId` | 赛事 `tid` |
| `sportId / sportName` | 赛事 `csid / csna` |
| `matchName` | 赛事 `tn`（优先全称） |
| `playId / playName` | 玩法 `hpid / hpn` |
| `marketId / marketValue` | 盘口 `hid / hv` |
| `playOptionsId / playOptions` | 选项 `oid / ot` |
| `odds / oddFinally` | 最新盘口 `oddsValue`；十进制赔率 = `oddsValue / 100000` |
| `playOptionName` | 选项展示文本 `otv`（缺失时才回退 `onb/on`） |
| `placeNum / matchType` | 抓包中的上下文值；`matchType` 不是仅靠赛事对象字段就能可靠推导，必须由选中市场上下文提供 |
| `marketTypeFinally` | 本次样本为 `EU`，应与当前赔率格式一致 |
| `betAmount` | 测试调用方输入 |
| `scoreBenchmark` | 当前玩法对应的比分基准；样本中全场让球为空字符串，不应无条件复制赛事比分 |

投注请求外层样本结构为 `useAcceptOdds=1, deviceType=2, preBet=0, seriesOrders[0].seriesType=1, seriesSum=1, fullBet=0, openMiltSingle=1`，并包含运行时 `deviceImei` 与 `cuid`。这两个值不得硬编码。

### 响应判断

`bet` 成功样本的外层 `code=0000000`，结果在 `data.orderDetailRespList[]`，其中包含 `orderStatusCode`、`betMoney`、`oddsValues` 等。不要只根据 HTTP 200 判断成功；还要校验业务 `code` 和明细状态。

## 5. 安全与实现边界

- `endpoints.json` 只保存域名、路径和环境入口，不保存 Cookie、Token、用户 ID、设备标识或密码。
- 普通赛事刷新脚本继续只读；投注测试独立由 `op7_test_bet.py` 执行。
- 投注脚本默认只生成脱敏请求体，不发网络请求。只有同时传入 `--submit-test`、设置 `OP7_TEST_BET_ENABLED=1`，并将目标主机限定在本地/测试白名单后才会发起一次调用。
- 对非本地域名，必须显式配置精确的 `OP7_TEST_BET_ALLOWED_HOSTS`；不得把生产域名加入白名单。
- 投注前先刷新赔率、校验市场状态和最小金额；不自动重试投注 POST，避免不确定结果导致重复下单。
- 本次没有从 ChatGPT 环境向任何接口发起投注请求；只依据 HAR 做离线分析并实现本地测试流程。
