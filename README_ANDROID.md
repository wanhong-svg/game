# OP7 Android 原生 APP 自动化

本目录用于将原有 OP7 数据采集程序的 APP 控制层迁移到 Android 原生自动化。目标包名：`com.seaapp.prod`。

## 当前迁移状态

- [x] 建立 Appium + UiAutomator2 Python 会话模块
- [x] 建立原生 APP 页面/控件树检查脚本
- [ ] 从模拟器运行时确认赛事列表请求的真实 URL、HTTP 方法、请求参数及响应结构
- [ ] 建立只允许赛事列表/赛事详情只读请求的 APP API 适配器
- [ ] 将 APP 返回数据接入现有 `op7_fb_eb_venue_mapper.py` 的标准化/去重逻辑
- [ ] 增加离线样例与回归测试

## 1. 环境准备（macOS）

确认模拟器与 Appium Server 已启动：

```bash
adb devices -l
curl http://127.0.0.1:4723/status
```

安装 Python 依赖：

```bash
python3 -m pip install -r requirements-android.txt
```

## 2. 检查原生 APP

```bash
python3 scripts/inspect_op7_app.py
```

可选择将原生控件树保存到本机临时文件（不要提交，可能含账号相关文本）：

```bash
python3 scripts/inspect_op7_app.py --output /tmp/op7-native-ui.xml
```

脚本不会点击投注、下单、钱包或资金相关控件，也不会自动处理验证码/MFA。

## 3. Appium 自动刷新赛事列表

先在模拟器中手动登录 OP7，并停留在赛事列表页面，将列表滚动到顶部。启动一次下拉刷新：

```bash
python3 scripts/refresh_op7_event_list.py --count 1
```

每 20 秒刷新一次，共刷新 10 次：

```bash
python3 scripts/refresh_op7_event_list.py --count 10 --interval 20
```

持续刷新直到 Ctrl+C：

```bash
python3 scripts/refresh_op7_event_list.py --count 0 --interval 20
```

脚本通过 UiAutomator2 的屏幕手势在列表区域执行下拉，不依赖尚未确认的控件 ID；如果 APP 的列表不是标准下拉刷新布局，可使用 `--top-ratio`、`--height-ratio`、`--width-ratio`、`--swipe-percent` 调整手势区域。该脚本只发送 UI 手势，不会读取或请求赛事 API，也不会点击投注/账户控件。

## 4. 在 APP 内搜索命令行指定的赛事

此功能通过 Appium 操作 OP7 当前界面的原生搜索框，不调用外部赛事 API。先在模拟器中打开 OP7 的赛事列表页面，然后执行：

```bash
python3 scripts/search_op7_event.py --event "国王" --league "NBA" --home "萨克拉门托国王" --away "波士顿凯尔特人"
```

先在 OP7 中手动打开搜索面板（当前截图中的搜索输入框没有可用 resource-id）。脚本会寻找唯一可见的 EditText；若存在 resource-id 则按 ID 定位，否则只在可见输入框恰好一个时使用该控件本身，不使用固定坐标。输入并按 Enter 后解析搜索结果。可进一步提供联赛与两队名称，让脚本按唯一结果行的 accessibility label 动态点击，而不是使用固定 adb tap 坐标：

```bash
python3 scripts/inspect_op7_app.py --output /tmp/op7-native-ui.xml
```

如有多个输入框且无法唯一识别，再根据控件树中的真实 `resource-id` 指定搜索入口和输入框：

```bash
python3 scripts/search_op7_event.py --event "赛事名称" --search-trigger-id "实际搜索按钮resource-id" --search-field-id "实际输入框resource-id"
```

如只想填入关键字、不提交搜索，可加 `--submit none`。当前界面没有可安全唯一识别的搜索框时，脚本会列出候选控件并停止，不会猜测控件或误操作其他输入框。搜索结果结构仍需在实际模拟器中验证。

## 5. 赛事列表接口发现（仍需真实运行证据）

Appium 负责启动 APP 和控制原生 UI；Appium 本身不会自动提供 APP 的 HTTP 请求记录。必须从 `com.seaapp.prod` 的真实运行流量或应用可用的诊断信息中确认赛事列表接口，不能把浏览器端接口或猜测 URL 当作 APP 接口。

推荐顺序：

1. 在模拟器中手动打开赛事列表，等待列表加载完成。
2. 若 APP 是 debuggable 构建，在 Android Studio 的 App Inspection / Network Inspector 中选择模拟器与 `com.seaapp.prod`，记录赛事列表请求。
3. 若使用经授权的 HTTPS 调试代理，确认该构建和站点允许代理检查；不要绕过证书固定（certificate pinning）、设备完整性或其他应用安全控制。若代理不可用，改用应用开发方提供的调试日志/API 文档。
4. 对请求记录脱敏后，仅保留：请求 host/path、method、必要的非敏感参数名、响应 JSON 结构、分页字段、赛事 ID/主客队/开赛时间/状态字段。
5. Cookie、Authorization、Token、设备标识、用户 ID、签名密钥和完整请求头不得放入仓库、聊天或日志。

确认接口前，不创建伪造的 `APP_EVENT_LIST_URL`，也不重放未经确认的请求。若接口需要会话凭据，应只从本机受控会话获取，并且不落盘到 Git。

## 6. 赛事列表响应适配器

`android/op7_event_adapter.py` 提供离线、可配置的 JSON 响应适配器。由于当前执行环境无法连接你的 Android 模拟器，也没有真实 APP Network Inspector 导出记录，**真实赛事列表 URL、HTTP 方法、响应字段路径尚未确认**；示例映射文件仅为模板，不能直接视为真实接口。

确认响应结构并脱敏后，复制 `android/event_list_mapping.example.json`，按真实响应填写 `events_path` 与字段路径。适配器会输出 mapper 风格赛事行，并严格拒绝空赛事列表、缺少赛事 ID/主客队或无效开赛时间。它不发起网络请求、不处理认证信息，也不会猜测 API 地址。

## 7. 后续适配器约束

接口确认后，APP 数据源适配器将遵循以下约束：

- 仅允许经确认的赛事列表、赛事详情等只读端点。
- 对 host、path、method 做显式白名单；禁止任意 URL。
- 只读取赛事/盘口数据，不调用投注、订单、钱包、充值、提现或资金变更接口。
- 保留原始状态值；未确认的状态枚举不做推断。
- 记录抓取时间、分页信息、响应数量和错误状态；不得把空响应当成成功的零赛事。
- 通过适配层把赛事字段转换为现有 mapper 可处理的数据结构，避免重写成熟的解析/去重逻辑。

## 8. 运行配置

默认值：

- Appium Server：`http://127.0.0.1:4723`
- Android UDID：`emulator-5554`
- APP 包名：`com.seaapp.prod`

可通过环境变量覆盖：

```bash
export APPIUM_SERVER_URL=http://127.0.0.1:4723
export ANDROID_UDID=emulator-5554
export OP7_APP_PACKAGE=com.seaapp.prod
```


## 9. 从 Android 模拟器抓取 OP7 网络流量（接口发现）

运行前确保模拟器已启动、OP7 已登录，并停留在赛事列表页面。执行：

```bash
python3 scripts/capture_op7_network.py --duration 90
```

脚本会检查设备、APP 包名及模拟器内的 `tcpdump`，将抓包保存到本机 `/tmp/op7-network-时间戳.pcap`。抓包期间请在 OP7 中打开或刷新赛事列表。也可指定输出路径：

```bash
python3 scripts/capture_op7_network.py --duration 90 --output /tmp/op7-events.pcap
```

**能力边界：** 这是模拟器网络接口的 PCAP 抓取，不是已解密的 HTTP 请求/响应。HTTPS 的 URL path、请求体和赛事 JSON 仍受 TLS 加密保护；脚本不会绕过证书固定、TLS 或设备安全机制。当前抓取范围可能包含模拟器内其他 APP 的网络流量，PCAP 也可能含敏感元数据，请仅在本机检查，不要提交到 Git 或直接公开。

如果 OP7 是可调试构建，优先使用 Android Studio Network Inspector 查看该进程的请求与响应；否则请使用应用开发方提供的调试日志/API 文档。只有在合法授权且调试构建明确允许的情况下，才能进一步取得明文响应。确认真实赛事列表请求后，再将其 host/path/method 和响应字段映射接入 `android/op7_event_adapter.py`。不要把 Cookie、Authorization、Token、签名或完整请求头保存到仓库。


## 10. OP7 赛事、盘口与注单面板三层只读采集

已拆分为三个独立模块：

- `android/op7_event_search.py`：解析赛事搜索结果和详情页赛事头部；按联赛、主客队、比赛节次、双方比分及比赛时钟严格核验。主客场顺序相反时会按球队身份调整比分后比较；两端时钟必须可读取且差值不超过 120 秒，联赛、节次、比分不一致或进度信息不足均拒绝匹配。搜索列表中的日期（例如 11-22）不会被误解析成比分。
- `android/op7_market_reader.py`：按盘口标题识别全场独赢、全场让分/让球、全场大小，再读取选项方向、盘口线和赔率。目标盘口必须唯一，例如 `FULL_TIME_HANDICAP + 主 +12.5`；不使用固定屏幕坐标。
- `android/op7_selection_verifier.py`：读取点击盘口后出现的注单面板，核对联赛、主客队、盘口类型、投注方向、盘口数值、赔率；可选核对比分。任何关键字段不一致即停止采集。

模块提供纯解析函数和基于 Appium accessibility label 的精确定位辅助函数。赛事搜索结果按联赛与主客队唯一定位后，直接调用对应 UI 元素的 click；盘口选项也按完整 accessibility label 定位。整个流程不使用固定屏幕坐标或固定 adb tap；点击盘口选项只用于打开注单面板做二次读取；**不会填写金额，不会点击“投注”，不提交订单**。如果界面控件没有可唯一识别的 accessibility label，程序会失败关闭，不会退回固定坐标。

运行离线单元测试：

```bash
python3 -m unittest discover -s tests -p 'test_op7_ui_modules.py' -v
```

当前模块已依据你提供的控件树和截图结构编写，但尚未在你的模拟器上实机回归。尤其需要在 Appium 实测中确认：赛事详情比分/节次控件、盘口选项的完整 content-desc，以及注单面板在不同赛事和盘口下是否保持同一字段结构。未经这一步，不把采集结果标记为生产可用。
