# EB Under × Pingbo NBA 实时赔率校验

此流程只读取赔率并生成匹配/计算报告，不调用投注、订单、钱包或资金接口。

## 启动顺序

终端 1：启动现有 EB/OP7 只读会话刷新（登录会话保存在本机）：

```bash
python op7_fb_eb_pingbo_live_refresh.py --live --eb-max-events 50
```

刷新器会保留 EB 赛事的联赛/运动元数据，并将 EB 赛事列表限定为 NBA，抓取其大小分盘口快照。

终端 2：启动统一校验：

```bash
export PINGBO_API_BASE_URL="https://www.pin135.com"
# 需要登录态时仅在本机设置，不要提交到 Git
export PINGBO_READONLY_HEADERS_JSON='{"Cookie":"本机测试会话Cookie"}'
python eb_pingbo_nba_validator.py --watch --interval 20
```

单次校验可去掉 `--watch`。程序读取 `live_snapshots/EB_events_latest.json` 和 `EB_markets_latest.json`，并通过 Pingbo NBA 列表/详情接口获取平博赔率。

## 匹配与交叉赔率规则

- EB 普通全场大小与全场大小-附加盘都进入校验和候选池；两者均使用 `hpid=38`，必须结合 `hpn/topKey` 区分。
- 普通全场大小：`market_name=全场大小`、`market_top_key=38`。
- 全场大小-附加盘：`market_name=全场大小-附加盘`、`market_top_key=38-1`。
- 两种玩法的每条 `hl` 盘口线及其 `hid/hv/ol` 都独立保留，不能互相覆盖。
- Pingbo 只读取 NBA 赛事详情的全场盘口 `periods["0"].overUnder`。
- 赛事以标准化后的主客队、开赛时间和比赛进度匹配；不确定时不做猜测。
- 盘口入口条件：EB 全场大小分数 >= Pingbo 全场大小分数。
- 已关闭、离线或赔率无效的盘口不参与计算。

统一使用十进制赔率，阈值严格小于 `1.0052`：

`A = 1 / Pingbo_Over + 1 / EB_Under`

- 仅 A < 1.0052：允许投注 EB Under，并将 EB Under 赔率作为唯一候选下注赔率，输出允许日志。
- `B = 1 / Pingbo_Under + 1 / EB_Over` 仅 B < 1.0052：输出 EB 禁止日志，并记录对应 EB Over 赔率；不生成投注候选、不允许投注 EB Over。
- A、B 同时小于 1.0052：标记为双向同时满足异常，不生成候选。
- A、B 均不小于 1.0052：不输出赔率匹配结果或失败日志。

只有 A 方向能够进入 EB Under 候选下注流程；B 方向只作禁止方向诊断。程序的实时投注前复核同样执行该规则。

## Pingbo 页面接口行为分析（新增）

启动 `op7_fb_eb_pingbo_live_refresh.py --live` 后，刷新器会监听 Pingbo 页面自动发出的：

- `/sports-service/sv/mobile/odds-live`
- `/sports-service/sv/mobile/odds-today`

同时，`pingbo_nba_detail.py` 在脚本请求这两个接口时，也会把实际组装参数和响应写入同一份本地追踪文件。默认位置：

`live_snapshots/pingbo_api_analysis/pingbo_api_trace.jsonl`

每行是一条独立 JSON 记录，包含记录时间、请求来源、接口路径、请求参数、HTTP 状态、响应数据；页面端还会记录响应时页面可见文本，以及轮询观察到的页面文本变化。脚本请求记录会读取最近一次对应页面请求样本，输出参数对比：页面独有参数、脚本独有参数、同名参数值差异。

注意：
- 当前 `PingboClient` 的赛事列表实现实际请求 `odds-today`，并未主动请求 `odds-live`；因此 `odds-live` 会记录页面样本，但没有脚本请求样本时不会伪造对比结果。
- 页面文本变化与最近一次赔率接口响应会带时间关联，属于观察证据，不代表已证明接口字段与页面某个赔率变化之间的因果关系。
- 不记录 Cookie、Authorization、Token、密码等认证信息；日志只写入本机，不提交到 Git。响应体可能较大，请在停止采集后将整个 `pingbo_api_analysis` 目录打包或直接提供 JSONL 文件供后续分析。
- 如需自定义目录，可在启动脚本前设置 `PINGBO_API_ANALYSIS_DIR`；页面监听脚本默认使用 `--out` 目录下的 `pingbo_api_analysis`，建议保持两者一致。

## 输出文件

- `eb_pingbo_nba_validation.json`：完整结构化结果。
- `eb_pingbo_nba_validation.md`：可读表格，展示赛事、盘口、双方赔率、合并概率、Edge 和判断。
