# Pingbo 增量赔率缓存（feature/h5-validation）

## 设计约束

- 缓存按 API 路径隔离，odds-today 与 odds-live 不共享快照。
- 赛事主键为 event_id；盘口主键为 (market_type, period, lineId)。
- 每条盘口保存 points、overOdds、underOdds、unavailable、offline、isAlt，以及赔率更新时间。
- refreshAll=true 是该数据源完整快照：只替换当前 source 的赛事/盘口集合。
- 已识别的非空事件增量按 event_id 合并，只覆盖增量中实际出现的盘口；未出现的盘口不删除。
- 空增量只更新 last_response_at、HTTP 状态和 version，不触碰赔率缓存及 odds_updated_at。
- 赔率变化比较 points、overOdds、underOdds、unavailable、offline；version 单独保存。
- 未知 update 数组结构只记录诊断，不猜测新增/修改/删除语义；不会将未知结构当作空快照或删除指令。

## 尚未确认的协议边界

目前浏览器页面 leagues[].events[] 完整快照结构已可识别；非空 update[] 的内部 schema、删除标记和盘口下架语义仍需真实样本验证。因此此实现采用保守合并：不会从未知 update 结构推断删除。确认协议后再增加明确的删除处理。

## 测试

运行：python -m unittest -v test_pingbo_incremental_cache.py

只读刷新器继续使用现有 --live 启动方式。此缓存层不调用投注或资金接口。
