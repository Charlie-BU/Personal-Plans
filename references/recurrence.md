# 重复任务、实例和地区日历

## 规则

只在 task 中存 recurrence，dtstart 必须等于首个 schedule.start；start/end 时间戳必须带偏移且有明确 IANA timezone。支持 RFC 5545 的 DAILY/WEEKLY/MONTHLY/YEARLY，INTERVAL、COUNT 或 UNTIL、BYDAY/BYMONTHDAY/BYMONTH/BYSETPOS/WKST 等，使用 python-dateutil 校验和计算。COUNT 与 UNTIL 不能共存。

```json
{
  "dtstart":"2026-10-19T19:00:00+08:00",
  "rrule":"FREQ=DAILY;INTERVAL=2",
  "rdates":[],
  "exdates":[],
  "day_filter":{"calendar_id":"cn_user_region","kind":"workday"}
}
```

calendar_id 使用有效稳定ID，不把示例当用户实际地区。day_filter 可省略，kind 支持 workday/restday/statutory_holiday。

- 每两天：从明确锚点按自然日间隔，不随完成时间漂移。
- 每周一三五：FREQ=WEEKLY;BYDAY=MO,WE,FR。
- 每月31日：无该日期的月份不会自动改成月末，若用户要月末请用 BYMONTHDAY=-1。
- 日期型 dtstart 支持 YYYY-MM-DD；日期型 UNTIL 用 YYYYMMDD，带时间规则的 UNTIL 用明确 UTC 时间。
- rdates 显式增加、exdates 排除；与 dtstart 精度一致。规则修改需说明本次/以后/整个系列范围。

当前不实现“实际完成后 N 天”、秒/分/小时级周期和农历历法推算。用户提出这些需求时明确边界，可用已有工具核验一组具体日期或维护下一条单次计划，不伪装成锚点周期。不得在未来日期未知时编造提醒。

## 独立实例

agenda 对有限窗口生成虚拟 occurrence，不预写无限未来。ID 为 `occ_` + SHA-256(task_id + 换行 + 原始 recurrence_id) 前32位。原始实例时点使用系列时区的规范日期/带偏移时间，不随改期改变。每个实例是当时规则的快照，有自己的优先级、状态、备注、安排和期限。

只有真正安排、备注、完成或延期某次时 materialize；先 read 系列哈希，传纯实例字段，经校验发布后再执行下一动作。改期移出窗口、取消或跳过的持久化实例覆盖虚拟实例，不会在原日期复活。重复模板不标整系列 completed。

修改“本次及以后”用 split-series：以明确 cutover 给旧规则添加 stop_before，保留过去规则和实例；用新 ID 建新系列。对已经安排的未终态未来实例，先告知范围，用户选择保留拒绝处理或 cancel_open 在同提交取消，再分别处理外部日历/通知。完成、取消和跳过的历史实例不被重写。新记录可用 references 指回旧系列。调整整个系列不能悄悄改写过去；先明确用户要修正历史事实还是只改未来。

尚无持久化实例时也不直接用字段更新绕过时间保护；新开始时点可通过明确版本切分处理。详细参数见 operations.md。

## 地区工作日

周一到周五与官方工作日不同；法定节日与整段放假调休也不同。按用户地区和年份，使用有公开来源URL、version、verified_at 的日历数据。

`plans/calendars/holidays/<calendar_id>/<year>.json` 的 days 明确列 workday / statutory_holiday。restday 是 not workday，周末补班可 workday=true。某天无覆盖报错，不套用普通周末作为官方数据；跨年未知年份保持未确定。脚本仅在实际候选日期需要时要求相应年度文件。

## 时区与边界

查询日期以 vault 时区为准，规则按各任务时区展开并按重叠过滤。实例期限相对首个时点按本地墙钟迁移，保留当地日程节奏；没有 due_at 不凭空加截止时间。夏令时不存在/重复的规则时点必须显式处理：用 exdates 排除歧义规则时点，必要时用带偏移的 rdates 明确另一个合法时点；不能悄悄移动一小时。

单查询跨度不超过366天，每段重复展开最多2000项，总agenda最多10000项，超限报错并收窄；没有静默截断。跨午夜、偏移期限、月末、原始月份例外均需测试。

暂停/取消系列会停止当前视图中的虚拟展开；已物化实例保留。复盘暂停前的未物化安排时须读暂停前的历史快照，不能用当前暂停状态推断过去没有安排。需要只改变未来规则时用 stop_before 与 split-series，不用暂停代替版本切分。
