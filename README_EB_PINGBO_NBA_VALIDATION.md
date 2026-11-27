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

## 匹配规则

- EB 普通全场大小与全场大小-附加盘都进入校验和候选池；两者均使用 `hpid=38`，必须结合 `hpn/topKey` 区分。
- 普通全场大小：`market_name=全场大小`、`market_top_key=38`。
- 全场大小-附加盘：`market_name=全场大小-附加盘`、`market_top_key=38-1`。
- 两种玩法的每条 `hl` 盘口线及其 `hid/hv/ol` 都独立保留，不能互相覆盖。
- Pingbo 只读取 NBA 赛事详情的全场盘口 `periods["0"].overUnder`。
- 赛事以标准化后的主客队顺序匹配；不确定时不做反向猜测。
- EB Under 213.5 对应 Pingbo Over 213.5；盘口线必须完全一致。
- 已关闭、离线或赔率无效的盘口不参与计算。

## 计算

统一使用十进制赔率：

`combined = 1 / EB_Under + 1 / Pingbo_Over`

`Edge = 1 - combined`

`theoretical_roi = Edge / combined`

默认阈值为 0%，即 Edge 必须大于 0 才显示 RULE_PASS。可用 `--min-edge-percent 0.5` 设置至少 0.5% 的 Edge。结果同时输出双方隐含概率、合并概率、Edge 和理论 ROI。

这只是两边赔率的数学筛查，不含手续费、限额、赔率变化、结算规则差异等因素；实际可执行性仍需单独核验。程序不会自动投注。

## 输出文件

- `eb_pingbo_nba_validation.json`：完整结构化结果。
- `eb_pingbo_nba_validation.md`：可读表格，展示赛事、盘口、双方赔率、合并概率、Edge 和判断。
