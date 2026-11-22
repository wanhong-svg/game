# OP7 + Pingbo 双端监控实现说明

本分支新增双端监控基础框架，按确认的五个阶段推进。所有平台动作限定为启动/查看页面、人工登录、搜索及只读赛事/赔率采集；不包含下注提交、订单修改、钱包或资金接口。

## 已落地的基础能力

1. **双端会话**
   - Appium 连接已启动的 Android 模拟器及已安装的 OP7（默认包名 `com.seaapp.prod`）。
   - Playwright 启动可见 Chromium 并打开 Pingbo 登录入口。
   - 用户在两端手动登录；程序输出启发式页面状态并要求用户确认后才继续。
   - 不读取、保存或输出密码、Cookie、Token。
2. **赛事唯一匹配核心**
   - 标准化主客队名称，要求双方球队严格对应（允许主客队反序）。
   - 开赛时间差默认不超过 300 秒。
   - 只有双向唯一的一对一匹配才返回 `UNIQUE`；无匹配或多候选均拒绝。
3. **统一盘口类型**
   - `FULL_TIME_MONEYLINE`：全场独赢
   - `FULL_TIME_TOTAL`：全场大小
   - `FULL_TIME_HANDICAP`：全场让球
   - 只映射明确的已知标签；未知玩法不推断。
4. **赔率差值与持续监控引擎**
   - 同一盘口类型、比赛阶段、盘口线、投注选项才允许比较。
   - 差值定义为 `OP7赔率 - Pingbo赔率`。
   - 两端观测时间都必须在新鲜度阈值内，默认 30 秒；否则拒绝比较并产生告警。
   - 监控循环接受只读采集适配器；平台页面专用采集器必须基于实际 DOM/控件结构确认后再接入。
5. **SQLite 记录和规则**
   - 保存赔率快照、异常告警。
   - 每场赛事每种玩法只允许一笔记录（唯一键：赛事 + 玩法，不因盘口线不同而重复）。
   - 记录金额可选校验 50–500；该记录不是下注，也不会提交订单。

## 安装与启动

在项目根目录：

```bash
python3 -m pip install -r requirements-dual-platform.txt
python3 -m playwright install chromium
```

先启动 Appium Server。Android 模拟器可提前手动启动；也可配置 AVD 名称，由脚本尝试启动：

```bash
export ANDROID_AVD_NAME='你的AVD名称'
export ANDROID_UDID='emulator-5554'
```

确认 Android Platform Tools 与 Appium Server 可用：

```bash
adb devices -l
curl http://127.0.0.1:4723/status
```

启动程序后，终端会主动询问 Pingbo 网站地址，不需要预先写入配置文件或仓库。Pingbo 登录后，程序会调用仓库现有的赛事列表接口 `/sports-service/sv/compact/events`，从接口结果提取赛事，不再要求用户在 Pingbo 页面逐场搜索：

```bash
python3 scripts/dual_platform_monitor.py
```

终端交互示例：

```text
请输入 Pingbo 网站地址（登录入口 URL）：https://你的Pingbo登录入口
```

也支持可选参数 `--pingbo-url` 或环境变量 `PINGBO_URL`，但默认流程是在命令行现场输入。程序会连接 OP7、打开 Pingbo 浏览器，等待用户手动登录并确认。登录后调用现有接口并将赛事列表写入 SQLite。

接口参数优先读取本地 `refresh_config.json` 的 `part567.events_params`；若本地文件不存在，则读取仓库示例配置中的 Pingbo 参数。若站点返回 401/403，请检查登录会话、账号权限及请求头配置。接口域名和路径仍遵守现有脚本白名单，不会把用户输入的网址拼接成任意 API 请求。

## 尚未接入真实环境的部分

当前仓库已有 Pingbo 赛事列表接口，但尚未取得 OP7 Android 端真实赛事列表响应的实机样本，也没有 OP7/Pingbo 盘口控件的实机快照。因此：

- 目前双端启动、手动登录确认和离线核心逻辑已经建立。
- Pingbo 赛事列表接口已接入，使用现有 `op7_fb_eb_pingbo_live_refresh.py` 的请求白名单、响应解码与 `op7_fb_eb_venue_mapper.py` 的赛事解析逻辑。
- Pingbo 赛事列表会在登录确认后获取并保存到 SQLite 快照。
- OP7 Android 端赛事列表的实机数据来源仍需接入；因此跨端唯一匹配尚未在真实环境端到端完成。
- OP7/Pingbo 的三类盘口字段映射及真实页面持续赔率采集仍待实机验证。
- 不猜测 OP7 Android 的接口 URL、字段或 resource-id；需用脱敏页面结构/控件树或经确认的只读响应实现 OP7 赛事适配器。
- 在真实采集适配器接入前，监控循环不应对真实比赛启动；核心比较逻辑可用离线测试数据验证。

## 离线测试

```bash
python3 -m pytest tests/test_dual_platform_core.py -q
```

测试覆盖严格唯一赛事匹配、歧义拒绝、三类盘口映射、赔率差值、盘口线不一致拒绝、数据记录唯一约束和金额边界。
