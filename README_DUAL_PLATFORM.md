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

启动程序后，终端会主动询问 Pingbo 网站地址，不需要预先写入配置文件或仓库：

```bash
python3 scripts/dual_platform_monitor.py
```

终端交互示例：

```text
请输入 Pingbo 网站地址（登录入口 URL）：https://你的Pingbo登录入口
```

也支持可选参数 `--pingbo-url` 或环境变量 `PINGBO_URL`，但默认流程是在命令行现场输入。程序会连接 OP7、打开 Pingbo 浏览器，输出两端登录状态提示。请手动登录两端，确认成功后在终端按 Enter。

## 尚未接入真实环境的部分

当前仓库证据尚未提供 Pingbo 登录 URL、赛事搜索控件/结果 DOM、赔率列表 DOM，也没有 OP7 当前搜索结果和盘口控件的实机快照。因此：

- 目前双端启动、手动登录确认和离线核心逻辑已经建立。
- 双端真实赛事搜索结果提取、OP7/Pingbo 的三类盘口字段映射，以及真实页面持续赔率采集尚未宣称完成。
- 不猜测站点 URL、CSS selector、Android resource-id 或网络接口；需用脱敏页面结构/控件树确认后实现平台适配器。
- 在真实采集适配器接入前，监控循环不应对真实比赛启动；核心比较逻辑可用离线测试数据验证。

## 离线测试

```bash
python3 -m pytest tests/test_dual_platform_core.py -q
```

测试覆盖严格唯一赛事匹配、歧义拒绝、三类盘口映射、赔率差值、盘口线不一致拒绝、数据记录唯一约束和金额边界。
