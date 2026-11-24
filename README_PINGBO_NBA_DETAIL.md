# Pingbo NBA：列表页后台获取详情大小分

基于 `www.pin135.com.har` 的真实 HAR 请求链实现。页面无需从赛事列表跳转到详情页；客户端在后台直接请求相同 JSON 接口。

## HAR 中确认的接口链

| 顺序 | 接口 | 参数/用途 |
|---|---|---|
| 1 | `GET /sports-service/sv/mobile/sports` | 项目列表；篮球 `sportId=4` |
| 2 | `GET /sports-service/sv/mobile/leagues` | `sportId=4&periodNumber=0`；NBA `leagueId=487` |
| 3 | `GET /sports-service/sv/mobile/market-filters` | 篮球盘口筛选配置 |
| 4 | `GET /sports-service/sv/mobile/odds-today` | `sportId=4&periodNumber=0&periodNumbers=-1&betTypes=HDP,OU,ML_1X2&version=0` |
| 5 | `GET /sports-service/sv/mobile/event` | `eventId`、`version`、`specialVersion`、`locale`；首次详情请求使用版本 0 |
| 6 | `GET /sports-service/sv/mobile/league-events` | `leagueId=487`，返回该联赛赛事基本信息 |

NBA 过滤以篮球 `sportId=4` 和 NBA 的 `id=487` / `leagueCode=nba` / 精确联赛名为准，不通过球队名称猜联赛。

## 盘口结构

列表和详情的完整赛事对象均可在 `periods` 下读取盘口：

- `periods["0"].overUnder[]`：全场大小分。
- `periods["1"].overUnder[]`：上半场大小分。
- `periods["3"]`、`periods["4"]`：其他分段，不作为全场盘口。
- 每条全场盘口包含 `lineId`、`points`、`overOdds`、`underOdds`、`isAlt`、`unavailable`、`offline`。

HAR 样本魔术 vs 活塞的全场盘口中，`213.5` 对应 Over `-105`、Under `-109`。详情接口首次返回 `normal.periods["0"].overUnder`；后续增量轮询可能 `normal=null`，此时程序使用同一赛事列表响应中的完整盘口作为回退，不把空详情误认为没有盘口。

美式赔率转换为十进制赔率，仅用于统一规则比较：正赔率 `1 + odds/100`，负赔率 `1 + 100/abs(odds)`。

## 本地运行

```bash
export PINGBO_API_BASE_URL="https://www.pin135.com"
export PINGBO_REFERER="https://www.pin135.com/zh-cn/x/sports/today/basketball"
# 仅当测试环境要求登录时，在本机配置 HAR 会话请求头；不要提交 Cookie
export PINGBO_READONLY_HEADERS_JSON='{"Cookie":"本机测试会话Cookie"}'

python pingbo_nba_detail.py --out pingbo_nba_totals.json
```

程序只调用赛事/盘口读取接口。Cookie 仅从本地环境变量读取，不会写入文件或输出日志。浏览器页面可以保持在篮球赛事列表页。

## EB 反向盘口校验

`match_reversed_totals(eb_rows, pb_rows)` 按同一场比赛、全场结算、相同总分线匹配反向选项：EB Under 对 Pingbo Over，EB Over 对 Pingbo Under。支持本 HAR 示例涉及的中英文球队别名。不同赛事、不同盘口线、不同结算阶段不会匹配。

此处的“反向”指同一全场总分线上的 Over/Under 反向选项；不是把主客队身份交换。若上游 EB 数据是球队总分而不是全场总分，应先区分市场类型，不能套用该匹配函数。

本模块不调用投注、下单或资金接口。
