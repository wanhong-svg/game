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

## 3. 赛事列表接口发现（需要真实运行证据）

Appium 负责启动 APP 和控制原生 UI；Appium 本身不会自动提供 APP 的 HTTP 请求记录。必须从 `com.seaapp.prod` 的真实运行流量或应用可用的诊断信息中确认赛事列表接口，不能把浏览器端接口或猜测 URL 当作 APP 接口。

推荐顺序：

1. 在模拟器中手动打开赛事列表，等待列表加载完成。
2. 若 APP 是 debuggable 构建，在 Android Studio 的 App Inspection / Network Inspector 中选择模拟器与 `com.seaapp.prod`，记录赛事列表请求。
3. 若使用经授权的 HTTPS 调试代理，确认该构建和站点允许代理检查；不要绕过证书固定（certificate pinning）、设备完整性或其他应用安全控制。若代理不可用，改用应用开发方提供的调试日志/API 文档。
4. 对请求记录脱敏后，仅保留：请求 host/path、method、必要的非敏感参数名、响应 JSON 结构、分页字段、赛事 ID/主客队/开赛时间/状态字段。
5. Cookie、Authorization、Token、设备标识、用户 ID、签名密钥和完整请求头不得放入仓库、聊天或日志。

确认接口前，不创建伪造的 `APP_EVENT_LIST_URL`，也不重放未经确认的请求。若接口需要会话凭据，应只从本机受控会话获取，并且不落盘到 Git。

## 4. 后续适配器约束

接口确认后，APP 数据源适配器将遵循以下约束：

- 仅允许经确认的赛事列表、赛事详情等只读端点。
- 对 host、path、method 做显式白名单；禁止任意 URL。
- 只读取赛事/盘口数据，不调用投注、订单、钱包、充值、提现或资金变更接口。
- 保留原始状态值；未确认的状态枚举不做推断。
- 记录抓取时间、分页信息、响应数量和错误状态；不得把空响应当成成功的零赛事。
- 通过适配层把赛事字段转换为现有 mapper 可处理的数据结构，避免重写成熟的解析/去重逻辑。

## 5. 运行配置

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
