# OP7 / Part567 篮球盘口规则升级（包含加时）

## 适用范围
- Pinnacle：Part567（Pingbo）数据适配。
- Panda：OP7 EB（wire 标识 OB；程序显示 EB）数据适配。
- 赛事白名单：NBA、KBL。
- 允许的全场盘口：
  - `BASKETBALL_FT_HANDICAP`：全场让分，包含加时赛。
  - `BASKETBALL_FT_TOTAL`：全场大小分，包含加时赛。
  - `BASKETBALL_FT_MONEYLINE`：全场独赢/胜负（胜负平按来源实际选项结构解析），包含加时赛。
- 半场、单节、球员玩法及其他非全场玩法排除。
- 统一结算口径：`FULL_TIME + FULL_GAME_INCLUDING_OT`。

## 结算校验
配置中每个平台的 `settlement_scope` 默认 `UNKNOWN`，所以即使赛事和盘口类型识别成功，仍会输出 `NEEDS_REVIEW`。核实该平台对应全场盘口实际包含加时后，才可分别设置：

```json
"PINNACLE": {"settlement_scope": "FULL_GAME_INCLUDING_OT"},
"PANDA": {"settlement_scope": "FULL_GAME_INCLUDING_OT"}
```

若来源确认不含加时，输出 `SETTLEMENT_MISMATCH`，不得参与同盘口匹配。`ELIGIBLE` 仅表示通过本政策资格检查，不等于跨平台已匹配，也不代表赔率、盘口状态和赛事时间校验已完成。

## 盘口映射说明
- OP7 EB：篮球全场让分 `playId=39` / FB `mty=3002`；全场大小 `playId=38` / FB `mty=3003`；全场独赢 `playId=37` / FB `mty=3004`。
- Part567：已知 compact/events key `0` 为让分、key `1` 为大小分。独赢盘口的 key 与 tuple 字段布局必须从 HAR 原始数据核实后，填入 `BASKETBALL_FT_MONEYLINE.market_aliases.market_key`；程序不会猜测 key 或数组索引。

## 使用方法
先生成联合分析 JSON：

```bash
python op7_fb_eb_venue_mapper.py --har op7.har --har part567.har \
  --json-out op7_fb_eb_pingbo_analysis.json --report op7_fb_eb_pingbo_report.md
```

再运行篮球规则层：

```bash
python basketball_policy.py --analysis op7_fb_eb_pingbo_analysis.json \
  --config matching_config.example.json --out basketball_normalized.json
```

## 其他既定阈值
- 赛事开赛时间容差：5 分钟。
- 赔率过期阈值：30 秒。
- 汇率过期阈值：24 小时。
- 足球白名单：EPL、LaLiga、SerieA、Bundesliga、Ligue1、UCL。

## 安全与限制
仅读取本地 HAR/JSON，不发起网络请求，不执行下注、订单提交、钱包/账户/资金操作。赔率、时间、盘口状态及汇率需有可比较的原始字段才可校验；缺字段不得当作通过。该策略层输出来源标准化行与资格状态，不单凭规则层生成跨平台 `MATCHED` 配对。
