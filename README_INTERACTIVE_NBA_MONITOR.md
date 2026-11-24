# 交互式 NBA 实时盘口监控

本程序在本机启动可见浏览器，用户手动登录并打开 Pingbo 篮球赛事列表。终端输入“开始”后，程序在后台读取 NBA 赛事列表并逐场请求详情接口；输入“结束”停止扫描，输入“退出”关闭本程序启动的浏览器。

## 启动

```bash
python -m pip install -r requirements.txt
python interactive_nba_monitor.py
```

首次运行时会打开本地持久化浏览器配置目录 `browser_profiles/pingbo`。在浏览器中完成登录，切换到篮球赛事列表页，保持页面不动，然后在终端输入：

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

程序每 20 秒扫描一次。浏览器停留在当前页面，不会为了逐场获取详情而点击赛事或跳转页面。请求由同一个 Playwright BrowserContext 发出，复用该浏览器上下文的站点会话。

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

如果本机已启动 EB 只读刷新程序并生成 `live_snapshots/EB_events_latest.json` 与 `EB_markets_latest.json`，程序还会调用既有校验逻辑输出 EB Under 对 Pingbo Over 的盘口匹配、赔率、合并隐含概率和 Edge。

## 安全边界

- 仅调用 HAR 中确认的赛事列表、赛事详情、联赛和盘口读取接口。
- 不调用投注、下单、钱包、余额或资金接口。
- 不自动点击投注选项、不组装投注请求。
- 不读取或打印密码、验证码、Cookie 或 Token；浏览器会话仅保存在本机配置目录。
- 域名发现必须有已知 API 路径作为证据，且仅接受 HTTPS。
