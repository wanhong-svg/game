# OP7 FB/EB + Pingbo 抓包分析与只读实时刷新

## 文件

- `op7_fb_eb_venue_mapper.py`：离线解析 OP7 FB、OP7 EB、Pingbo HAR；输出赛事、盘口、赔率、状态、去重和跨站赛事候选。
- `op7_fb_eb_pingbo_live_refresh.py`：只读实时自动刷新。使用正常登录后的 Playwright 持久化浏览器配置；仅访问赛事列表、赛事详情及赔率读取接口。
- `refresh_config.example.json`：请求参数模板，不包含 Cookie、Token 或个人账户标识。
- `refresh_policy.json`：刷新周期、抓包证据及网络只读白名单。
- `op7_fb_eb_pingbo_analysis.json`：本次两份 HAR 的脱敏联合解析结果。
- `op7_fb_eb_pingbo_report.md`：字段完善及刷新频率分析报告。

## 一、离线抓包分析

```bash
python op7_fb_eb_venue_mapper.py \
  --har "op-1(1).har" \
  --har "pingbo-2(3)" \
  --json-out op7_fb_eb_pingbo_analysis.json \
  --report op7_fb_eb_pingbo_report.md
```

离线程序不会联网，也不会调用投注、订单、钱包、充值、提现或资金变更接口。

## 二、实时自动刷新

先复制参数模板：

```bash
cp refresh_config.example.json refresh_config.json
```

只在本机编辑 `refresh_config.json` 中标记为 `FILL_IN_LOCALLY...` 的字段。`euid`、`cuid`、`mcid` 等不要粘贴到聊天、日志或共享文件中。若接口参数已由当前页面自动生成，应以当前页面的有效只读请求为准。

安装依赖：

```bash
python -m venv .venv
source .venv/bin/activate
pip install playwright
playwright install chromium
```

### 1. 初始化登录会话（首次使用或会话过期）

选择单个平台：

```bash
python op7_fb_eb_pingbo_live_refresh.py --setup-session op7
python op7_fb_eb_pingbo_live_refresh.py --setup-session pingbo
```

也可以依次初始化两个平台：

```bash
python op7_fb_eb_pingbo_live_refresh.py --setup-session both
```

程序会打开可见 Chromium 浏览器。请在浏览器内自行输入账号密码并完成验证码/MFA；登录成功并进入正常业务页面后，回到终端按 Enter。程序关闭浏览器时，Playwright 会将持久化会话保存在本机：

- `browser_profiles/op7/`：OP7 会话
- `browser_profiles/pingbo/`：Pingbo 会话

程序不会读取、记录或上传密码、验证码、Cookie 或 Token。浏览器配置目录包含可复用的登录凭证，必须保留在受控本机，不能提交 Git、上传网盘或发给他人。

### 2. 启动后台只读刷新


启动只读刷新：

```bash
python op7_fb_eb_pingbo_live_refresh.py \
  --config refresh_config.json \
  --profiles-dir browser_profiles \
  --out live_snapshots \
  --live
```

指定最长运行 1 小时：

```bash
python op7_fb_eb_pingbo_live_refresh.py --config refresh_config.json --profiles-dir browser_profiles --out live_snapshots --live --max-runtime 3600
```

默认刷新周期：

| 数据 | 默认间隔 | 依据 |
|---|---:|---|
| FB 赛事列表 | 30 秒 | 本次 HAR 只有 17 次且间隔不稳定；30 秒是保守运行值，不是抓包确认值 |
| FB 单场盘口赔率 | 6 秒 | 详情请求样本中位约 5.8 秒，取整为 6 秒 |
| EB 赛事列表 | 5 秒 | `matchesPagePB` 请求间隔中位约 4.4 秒 |
| EB 单场盘口赔率 | 5 秒 | 按 `mid` 分组的重复赔率请求中位约 5 秒；不能用所有赛事请求混合后的 0.14 秒作为单场刷新周期 |
| Pingbo 赛事列表及内嵌盘口 | 20 秒 | 页面活跃初段请求约 19–23 秒；后续长间隔包含页面停留/非活跃时段 |

可以通过 `--fb-list-interval`、`--fb-odds-interval`、`--eb-list-interval`、`--eb-odds-interval`、`--pingbo-interval` 调整。所有间隔最小 5 秒，避免过度请求。

## 三、登录失效处理\n\n- 后台请求收到 HTTP 401 或 403 时，会将对应数据集状态记为 `AUTH_REQUIRED`，停止刷新并提示重新执行对应平台的 `--setup-session` 命令。\n- 401/403 可能表示会话失效，也可能表示账号权限不足；请先在可见浏览器确认登录状态和账号权限。验证码/MFA/风控页面需要用户本人按站点流程处理，程序不会尝试绕过。\n- 其他网络错误仍记录为 `FAILED`，不会自动当作登录过期。\n\n## 四、输出

程序只写入规范化的最新快照：

- `live_snapshots/FB_events_latest.json`
- `live_snapshots/FB_markets_latest.json`
- `live_snapshots/EB_events_latest.json`
- `live_snapshots/EB_markets_latest.json`
- `live_snapshots/Pingbo_events_latest.json`

每个数据集同时写入 `<name>_status.json`。成功时记录 `SUCCESS` 与观测时间；一般失败记录 `FAILED`；遇到 HTTP 401/403 时记录 `AUTH_REQUIRED`。使用快照的下游程序必须同时检查状态与 `fetched_at_utc`，不能只因文件存在就认定数据实时。

## 五、安全及边界

- 只允许 `api.738z3.com` 的 `getList/getMatchDetail`、`api.r4bud5u7.com` 的 `matchesPagePB/getMatchOddsInfoPB`、`www.pin135.com` 的 `compact/events`。
- 不会访问任何投注、下单、订单、钱包、充值、提现、cashout 或资金变更接口。
- 不读取或复用 HAR 中的 Cookie、Token、密码或会话头；使用用户本机正常登录的浏览器配置。不得绕过验证码或 MFA。
- 若会话过期、接口返回 401/403、站点要求验证码/MFA，应按站点正常流程重新登录。
- FB/EB 与 Pingbo 的赛事名称配对只是候选；盘口玩法、主客方向、结算范围、加时规则必须逐项确认后才可认为等价。
- 本次 HAR 没有证明 FB `getList` 的稳定自动刷新周期，因此 30 秒只是初始保守值，建议继续抓取至少 10–15 分钟的连续活跃页面数据再校准。
