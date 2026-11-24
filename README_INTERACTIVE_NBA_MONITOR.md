# 交互式 NBA 实时盘口监控

本程序在本机启动可见浏览器，用户手动输入 EB 模拟环境地址、登录并完成首单人工模拟投注确认后，再开始盘口识别。终端输入“结束”停止扫描，输入“退出”关闭本程序启动的浏览器。

## 启动

```bash
python -m pip install -r requirements.txt
python interactive_nba_monitor.py
```

首次运行时会打开本地持久化浏览器配置目录 `browser_profiles/pingbo`。浏览器启动后输入 EB 模拟环境 HTTPS 地址，在浏览器中手动登录并进入业务页面。输入“开始”后，程序已开始监听浏览器响应；请人工完成一笔首单模拟投注，然后在命令行输入“是”确认（尚未完成输入“否”继续等待）。

```text
开始
```

停止当前扫描：

```text
结束
```

退出程序：

```text
退出
```

程序每 20 秒扫描一次。浏览器停留在当前页面，不会为了逐场获取详情而点击赛事或跳转页面。请求由同一个 Playwright BrowserContext 发出，复用该浏览器上下文的 Cookie；同时从当前页面实际发出的 HAR 已知接口请求中，在内存捕获必要的动态业务请求头（如会话标识/签名头），用于详情读取。动态请求头不会打印或写入文件。若启动时浏览器已恢复旧标签页、未捕获到列表请求头，按终端提示在当前赛事列表页刷新一次即可。

## 首单域名与请求模板捕获

首单初始化时，监听器会同时捕获人工投注 POST 的请求结构、请求 API origin、Referer 对应的页面 origin，以及成功响应中的 orderNo。页面域名和 API 域名分别记录，不互相推断；后续可使用捕获的 origin 替换运行时域名。请求头中的 Cookie、Host、Content-Length 等传输或会话敏感字段会过滤，动态请求头仅保留在内存，不写入审计文件。审计文件只保存 origin、接口路径、状态码和订单号。

只有同时捕获到首单请求体、页面 origin、投注 API origin，且 HTTP/业务响应成功并返回非空 orderNo，首单初始化才通过。

## API 域名自动发现

启动扫描时，程序读取当前页面的 Resource Timing 资源 URL，仅将命中随附 HAR 已确认的接口路径加入域名发现结果：

- Pingbo：`/sports-service/sv/mobile/odds-today`、`odds-live`、`leagues`、`league-events`、`event` 等
- EB：`/yewu11/v1/m/matchesPagePB`、`matchDetail/getMatchOddsInfoPB`、`matchDetail/getMatchDetailPB` 等
- 公共赛事：`/api/match/sport/listbytype`、`/api/match/live/get100Refactoring`

只有观察到这些已知接口路径的 HTTPS 请求，才会把对应请求的 origin 写回 `endpoints.json`。不会仅因网页地址变化，就把网页域名盲目替换为 API 域名。更新记录写入 `runtime_discovery`，包括页面地址、更新时间和命中的接口数量。

当前 HAR 显示 Pingbo 的移动端赛事 API 与网页同属 `www.pin135.com`；EB 的接口域名与网页资源域名不同，不能互相混用。切换到新的测试环境后，先在页面正常加载赛事列表，使浏览器产生对应 API 请求，再输入“开始”即可发现新域名。

## 请求和数据

NBA赛事列表：
- `GET /sports-service/sv/mobile/odds-today`
- `GET /sports-service/sv/mobile/odds-live`

NBA赛事详情：
- `GET /sports-service/sv/mobile/event?eventId=...&version=0&specialVersion=0&locale=zh_CN`

详情盘口只读取 `normal.periods["0"].overUnder`，即全场大小分，不把半场盘口混入全场盘口。结果写入 `pingbo_nba_live_details.json`，并在终端逐场显示大小分盘口和赔率。

如果本机已启动 EB 只读刷新程序并生成 `live_snapshots/EB_events_latest.json` 与 `EB_markets_latest.json`，程序会执行以下规则：

1. 赛事必须是正规 NBA，双方球队按无序组合确认同一场比赛（主客顺序不作为盘口匹配条件），且 EB/Pingbo 开赛时间戳一致。
2. 滚球赛事还必须双方都明确处于滚球，并提供一致的规范化节次与秒级比赛进度；进度字段缺失或不一致时拒绝匹配，不猜测不同平台的状态枚举。
3. 盘口入口只读取 EB 全场小分，匹配同一场 Pingbo 全场大分，且仅当 `EB Under line >= Pingbo Over line` 时进入赔率计算。
4. 赔率双向交叉计算：
   - A：`1 / Pingbo Over + 1 / EB Under < 1.0052`
   - B：`1 / Pingbo Under + 1 / EB Over < 1.0052`
5. 仅 A 成立记录 EB 小分赔率；仅 B 成立记录 EB 大分赔率；两项同时成立标记异常；均不成立不生成候选。

结果写入 `eb_pingbo_nba_validation.json`。注意：当前程序只有在数据源提供明确的滚球节次/时钟字段时才会接受滚球匹配；缺字段会保守拒绝。

## 安全边界

- 仅调用 HAR 中确认的赛事列表、赛事详情、联赛和盘口读取接口。
- `op7_test_bet.py` 已具备独立的 EB 全场大小分原始字段映射、5 秒双边赔率复核接口、固定 11 元请求组装、orderNo/分转元解析和本地重复候选审计；投注 API origin 与 EB 只读 API origin 分开保存。\n- 自动化发送仍未接入本监控主循环：必须由当前已登录的浏览器会话提供真实 EB/Pingbo 双边刷新回调，以及首单捕获的动态请求上下文；缺少回调时 `--submit-test` 会拒绝发送。当前不得将其视为端到端自动投注已完成。
- 不读取或打印密码、验证码、Cookie 或 Token；浏览器会话仅保存在本机配置目录。
- 域名发现必须有已知 API 路径作为证据，且仅接受 HTTPS。
