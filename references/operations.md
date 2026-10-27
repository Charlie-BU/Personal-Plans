# 内存计算、变更与发布

所有个人包仅经工具 stdin/子进程 input 传入，不先写到本地 JSON，不把文本拼入 shell。`python3 -B <skill>/scripts/plans.py --stdin <command>` 不联网、不发布。

## 输入信封

```json
{
  "snapshot": "整个仓库当前40或64位提交SHA",
  "complete": false,
  "files_base64": {"plans/vault.json": "原始字节的base64"},
  "query": {"kind":"task","status":"pending"}
}
```

snapshot 是工具实际读取的 commit。查询可 partial，按 needs_files 补同快照文件；init/apply/configure/integration/verify 必须 complete=true，并实际完整取得 plans/。未有任何提交的仓库 init 可 snapshot=null；已存在共享仓库即使 plans/ 空，也必须用原 HEAD。其他目录不需下载，不参与删除或校验。

## 初始化

init 使用 settings：

```json
{"vault_id":"vault_example","timezone":"Asia/Shanghai","calendar":{"mode":"pending"}}
```

vault_id 用新唯一ID；时区取用户已确认值。已有 plans/ 不清空，不初始化陌生非空内容。脚本仅返回 prepared。记录真实 remote_saved 后继续 onboarding；calendar.mode=pending 不算日历连接成功。

## 计划操作

apply 的 plan：

```json
{
  "operation_id":"op_example",
  "base_commit":"同一仓库HEAD",
  "actor":"本轮操作者标识",
  "recorded_at":"2026-10-19T10:00:00+08:00",
  "changes":[
    {"action":"create","record":{
      "id":"task_language","kind":"task","title":"练习项目英文介绍",
      "summary":"为月末分享准备三分钟项目介绍。","body":"用户希望先练开场，再练问答。",
      "timezone":"Asia/Shanghai",
      "priority":{"quadrant":"important_not_urgent","source":"inferred","reason":"支持长期学习目标，用户未说明近期硬期限。"},
      "time_window":{"date":"2026-10-19","label":"晚上","estimated_minutes":30}
    }}
  ]
}
```

这里日期仅为字段示例；实际使用根据用户当前日期和时区计算。

一批最多100条变更；同一记录在同操作仅一次动作，有后续动作分次发布/回读。跨记录新增允许引用同批记录。

| action | 参数与用途 |
|---|---|
| create | record 最少 title/summary/kind/id/timezone/priority；创建 pending，默认字段由脚本补齐 |
| materialize | record 为 agenda 虚拟实例的纯记录字段，去掉 virtual/timing/original_timing/materialized/path；expected_series_sha256 来自 read 系列；实例内容必须与当前规则相同 |
| update | id + expected_sha256 + fields；修改标题、摘要、正文、关系、优先级等 |
| reschedule | id + expected_sha256 + reason + fields(schedule/due_at/time_window)；保留原期限 |
| complete | id + expected_sha256 + completed_at + completion_precision；不能完成整重复系列或未来时刻 |
| state | id + expected_sha256 + status + reason；开启、暂停、受阻、取消、跳过和重开；重开清除当前完成时间，历史仍保留 |
| note | id + expected_sha256 + text；追加备注 |
| delete | id + expected_sha256 + reason；先处理所有子项/依赖/系列实例，禁止悬空；已删ID不复用 |

已有记录均需 read 返回的 expected_sha256；original_due_at、身份、创建时间等不能直接 update。尚未实际完成时，不为了演示生成 complete。重复规则在已有持久化实例后不直接重写，系列版本操作见 recurrence.md。

## 配置

每天及周期备注通过 configure kind=period-note 追加，读取用 period-notes 命令；详见 [period-notes.md](period-notes.md)。每项计划的备注继续用 apply note，不新增重复存储。

configure 的 request 含 operation_id/base_commit/actor、可选 recorded_at、kind、value。凭据不进入配置值。

- calendar：`{"mode":"connected","provider":"实际服务","calendar_id":"已核对ID","verified_at":"实际回读时间戳"}`；也可明确退回 pending。
- holiday：schema_version=1、calendar_id、year、version、source_url（公开HTTPS）、verified_at、days。days 按日期列 `{workday:布尔, statutory_holiday:布尔}`。缺的日期不猜；可先提供已核验范围。
- onboarding：schema_version=1、stage、completed_steps（字符串数组）、reminders_enabled（布尔）。ready 的语义要求见 onboarding.md，不能用更新标记代替实际连接验证。
- reminders：schema_version=1、enabled、consent_reference、timezone、quiet_start/quiet_end（HH:MM）、max_per_day（1–100）；真实授权和宿主执行约束见 reminders.md。

## 本次及以后：系列版本

定时任务使用 configure 的 cronjob-defaults / cronjob / cronjob-binding / cronjob-run；字段、默认开关、领取与接管流程见 [cronjobs.md](cronjobs.md)。`cronjobs` 命令只按索引读取定义与绑定，不执行任务。

split-series 使用 request：operation_id、base_commit、actor、recorded_at、series_id、expected_sha256、cutover、future_policy、reason、new_record。

cutover 为系列时区的日期/带偏移时点；旧规则本身保留，仅添加不晚于既有边界的 stop_before，历史展开不改变。new_record 使用新 ID，提供新的 schedule 和 recurrence（dtstart 与 schedule.start 相同且不早于边界）；其他字段默认继承旧记录，created/revision由脚本重建。当前期限若未指定则按原相对偏移迁移。跨时区调整应显式给新 due_at，避免含糊。

future_policy=reject 会在存在未终态未来实例时拒绝；用户明确选择 cancel_open 后，在同一提交取消这些旧实例并建立新系列。已完成、已取消、已跳过实例保留。返回 cancelled_future_instance_ids，发布后还需撤销外部旧日历/通知并同步新版本。一次涉及超过100个记录须先缩小窗口/明确分批方案，不会部分发布。

## 报告归档

save-report 的 request：operation_id、base_commit、actor、recorded_at、report_id、from、to、as_of、可选 narrative（最多16000字符）。从完整快照重新计算指标，将 source_snapshot、口径、证据和解释保存到 plans/reports/YYYY-MM/<report_id>.json；报告ID不覆盖复用。输出仍为 prepared，回读后才能报告远端已存。

saved-reports --from 日期 --to 日期 按保存月份索引定位报告卡片（以 created_at 日期筛选）。按卡片 path 从同一快照取原报告并核对 sha256，再消费 narrative；指标基于 source_snapshot，不冒充当前实时状态。

## 查询、视图和集成

- query：query 对象见 retrieval.md；read ID：返回原文与哈希。
- agenda/report：`--from YYYY-MM-DD --to YYYY-MM-DD --as-of 带偏移时间戳`；agenda 可 --carry-over。report 返回统计与证据，不自动保存报告或推送。
- effects：`--from 日期 --to 日期 --state queued`，可多次 --state；按月份和状态读外部动作，返回 effect_executed=false。实际执行前仍需最新状态和外部去重核查。
- integration：request 契约见 calendar.md / reminders.md，返回 prepared 或已存在状态；不调用任何外部API。
- ics：与 agenda 同参数，返回 content；仅用户请求导出时可保存最终 .ics 交付文件，不将它当缓存/实时同步或触达证明。
- reindex：从完整且已校验的权威数据重建紧凑索引，详见 storage.md；输出 prepared，删除仅限派生文件，不改原始资料。
- verify：完整 plans/ 校验、重建比对派生索引，返回 valid；不能代替真实远端回读或账号权限验收。

## 发布与失败

prepared.files_base64 只包含新增/变化的 plans/ 文件，delete_paths 是明确删除（通常索引页数量变化）。先检查每条路径边界，再用现有仓库树为基础一次提交并非强制更新分支。所有写入遵循 storage.md。

同 operation_id 同内容重试可 already_applied；不同内容复用拒绝。HEAD 或旧哈希变化时重读并重建提案；超时未知先查原操作，不能换ID盲目重做。索引/manifest不一致意味着暂停写入排查；不手改生成文件来绕过校验。reindex 只重建派生索引，不修补损坏的原始资料，也不清洗历史。
