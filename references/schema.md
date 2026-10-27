# 数据约定 v1

实际 Python 校验与 record.schema.json 配合使用；JSON Schema 是跨 Agent 字段接口，脚本另验证关系环、完整目录、哈希和重复规则。默认值由 create/materialize 补齐；不是让用户填写长 JSON。

## 权威记录

每个 goal/milestone/task/occurrence 是独立 JSON 文件。id 是稳定的 ASCII 小写字母起始标识（最长96字符）；修改标题、安排或状态不换 ID。归档月份按原始 recurrence_id，不能改期后搬出原始月份而失去例外索引。

| 字段 | 语义 |
|---|---|
| schema_version / kind / id | 版本1；goal、milestone、task、occurrence |
| title / summary / body | 具体标题、路由摘要、完整原文/说明；摘要不作完整事实证据 |
| aliases / tags | 用户别名、检索表述；来源明确，不虚构用户偏好 |
| parent_id / depends_on | 层级与先后依赖独立；同提交新建可互相引用，无环和悬空 |
| priority | quadrant、source、reason 均必填；用户覆盖 Agent 暂定判断 |
| timezone | 明确 IANA 时区；不根据语言猜测 |
| period | 目标/里程碑的 start/end，YYYY-MM-DD 或 null；周期不限 |
| schedule | 已确定的 start/end；日期型 end 排他；时间戳必须带 UTC 偏移 |
| time_window | date、label 和可选 estimated_minutes；尚未定时的“今晚”保留原意 |
| due_at / original_due_at | 当前/最初期限；无期限可 null，不能靠更新字段擦掉历史 |
| status | pending/in_progress/blocked/completed/cancelled/skipped/paused；skipped 仅实例 |
| completed_at / completion_precision | 完成现实时间 + instant/date；仅 completed 时有值 |
| recurrence | 仅 task 有；详情见 recurrence.md |
| task_id / recurrence_id | 仅实例有；原始系列及原始时点，改期不改变 |
| notes | at、actor、text；用 note 追加，不直接覆盖列表 |
| references | namespace、id，可附 path/snapshot/note；不自动授权跨目录读取 |
| reminders | id、anchor=start/due、offset_minutes（提前为负）、channel 别名 |
| created_at / updated_at / revision | 记录时间与正整数修订；脚本维护 |
| measure | 可选 unit/target/current/method；无指标不强造数字 |

priority.quadrant：important_urgent、important_not_urgent、urgent_not_important、neither。
priority.source：user、inferred、inherited；reason 保存判断依据。脚本拒绝缺失优先级，但不替代 Agent 的具体判断。

## 日期与状态

时间戳示例 `2026-10-19T09:00:00+08:00`，日期示例 `2026-10-19`。日历 start/end 要同精度，结束严格晚于开始；跨午夜写真实次日。日期型 schedule 表示全天，不当作午夜会议；日期型 due_at 表示该记录时区当日结束。查询日期默认按 vault 时区匹配，跨时区排程按实际时间重叠。

多年规划使用 period；单个执行时间块不得超过366天。未排程、无期限的任务合法，但不会自动出现在某一天的日程。

生命周期与时间表现分开。timing 是查询时派生：not_due、overdue_unconfirmed、on_time、late、no_deadline、unknown_precision、excluded，不作为永久状态另存。到期不自动失败；日期级完成遇到同日精确截止时刻只能报告 unknown_precision。original_timing 对原期限另算。

reschedule 保留 original_due_at，记录原因和 before/after；state 变更终态后若要继续，先重开并说明原因。父项不自动随子项完成，子项也不随父项完成。measure.method=manual 时 current 为明确数值或未知；leaf_completion 的实时分子/分母由相关叶子项查询计算，不将未穷尽的进度写成精确百分比。

## 可复用的跨 Agent 元数据

vault.json 含 schema_version、id、revision、timezone、创建/修改时间、calendar（mode/provider/calendar_id/verified_at）。连接仓库地址、分支和凭据在 Skill 外；calendar 字段仅是非凭据身份与验证状态。

活动以操作 ID 和序号保存 action、record_id、actor、recorded_at、reason、关键字段 before/after，Git 仍保留完整文件版本。操作记录保存请求哈希、changed_ids 和 deleted_ids，避免未知结果重试或已删 ID 被误复用。

索引派生字段、路由和证据层次见 retrieval.md。外部服务意图与回执见 calendar.md / reminders.md。不要让某个 Agent 的聊天摘要、人格提示或本机缓存成为权威数据。
