# EB NBA 列表到详情：额外全场小分提取

本模块按 `eb-列表到-详情(1).har` 还原 EB 的只读请求链：

1. `POST /yewu11/v1/m/noLiveMatchesPagePB`：赛事列表（分页，响应 data 为 Base64 + Gzip）。
2. `GET /yewu11/v1/w/matchDetail/getMatchDetailPB?mid=...`：赛事详情。
3. `GET /yewu11/v1/w/category/getCategoryList?sportId=2&mid=...`：玩法分类。
4. `GET /yewu11/v1/m/matchDetail/getMatchOddsInfoPB?mid=...&mcid=0&cuid=...`：完整盘口和赔率。

## 识别规则

- `csid == "2"`，且 `tn/tnjc` 中存在的联赛标签必须精确等于 `NBA`。
- 不使用球队名称包含 NBA 的模糊匹配，避免把 NBA 2K/梦幻赛事纳入。
- 从赛事列表 `hps[]` 中收集 `hpid=38` 的 `hid`，作为列表页已经展示的全场大小盘口。
- 详情响应只提取 `hpid=38` 且 `hid` 不在列表页集合中的盘口。
- 只输出 `ot=Under` 的小分选项；同时保留赛事/球队 ID、`hid`、`hv`、`oid` 和原始赔率。
- EB 的 `ov` 整数赔率按 `ov / 100000` 转换。
- 不把上半场 `hpid=18`、单节大小或主/客队总分玩法混入全场总分。

## 离线重放 HAR（不会发出网络请求）

```bash
python eb_nba_detail.py --har "eb-列表到-详情(1).har" --out eb_nba_har_result.json
```

离线模式只处理 HAR 中已捕获的列表页和详情响应。HAR 的分页字段即使报告多页，也不会凭空生成未抓取页面；输出会注明缺失响应的赛事 ID。

## 本地只读实时查询

先从本机测试浏览器/测试会话安全地取得请求头，并在本机环境变量设置，不要写进仓库：

```bash
export EB_API_BASE_URL="https://api.r4bud5u7.com"
export EB_EUID="本地测试环境的 euid"
export EB_CUID="本地测试环境的 cuid"
export EB_READONLY_HEADERS_JSON='{"Origin":"https://cgweb.op7grey.com","Referer":"https://cgweb.op7grey.com/"}'
python eb_nba_detail.py --out eb_nba_extra_under.json
```

如果测试 API 需要 Cookie 或其他认证头，可将其仅放在本机的 `EB_READONLY_HEADERS_JSON` 环境变量中；不要贴到聊天、日志或提交到 Git。默认 API Host 来自 `endpoints.json` 的 `apis.eb.nba_read_base_url`，且必须属于 EB Host 白名单。可以用 `EB_API_BASE_URL` 覆盖，但仍会校验白名单。

可选参数：`--page-size`、`--max-pages`、`--max-events`、`--timeout`。正式扫描默认按赛事列表返回的 `pages` 翻页，并对每场正规 NBA 依次请求详情、分类、完整盘口。

## 安全边界

该脚本只允许赛事列表、赛事详情、玩法分类和赔率读取四个接口。它不包含投注、下单、订单、钱包或资金变更调用。它只提取候选盘口，不做 Pingbo 赔率规则判定，也不自动投注。
