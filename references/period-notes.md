# 计划、每日及周期备注

每个计划（goal/milestone/task/occurrence）已有 notes，使用 apply note 追加并 read 回读。重复实例的反馈先物化该实例，再记 note；不要把本次反馈写在模板上当作整系列结论。用户明确报告完成时按 complete 等生命周期动作保存实际状态，原始补充另用 note 保留。

每日和周、月、季度、年、自定义周期使用独立远端记录：
`plans/periods/<kind>/<start-year>/<SHA256(scope身份)>.json`。scope 身份由 kind/start/end/timezone/plan_id 组成，label 只是展示名，不因改名产生第二份记录。start/end 为用户时区的日期，均包含；周起点、财季或其他周期按用户与计划约定计算，不套用当前日期或猜边界。周期可跨年、跨多年，不受 agenda 单次查询窗口限制。

未填写也始终有可定位的备注入口，无需预建空文件。展示时预留“备注/补充”列，无内容用“—”；原文不静默截断，长说明按需展开。用户说不保存时不写入。

## 保存

复用 configure 原子写入：

```json
{"operation_id":"op_feedback_unique","base_commit":"实际仓库HEAD","actor":"实际操作者","recorded_at":"实际带时区记录时刻","kind":"period-note","value":{"scope":{"kind":"quarter","start":"2026-10-01","end":"2026-12-31","timezone":"Asia/Shanghai","label":"第四季度","plan_id":null},"text":"用户原话及补充说明","basis":"user_statement","completion":"用户明确描述的完成情况；没有则null"}}
```

kind=day/week/month/quarter/year/custom；day 的起止必须同一天。plan_id=null 表示整段生活/计划周期的反馈；给某一计划的周期反馈则填稳定计划 ID，并要求该计划存在。日期只是示例。completion 可为字符串或 null，不强制用一个完成率概括整个周期。

每次追加保存 at、actor、source_snapshot、basis、text、completion；basis=user_statement 或 agent_observation，后者必须在文字中说明观察依据、统计范围和未知项，不能伪装成用户自述。用户回复由 Agent 代写时 basis 仍是 user_statement，actor 保留实际操作者，不冒充直接用户写入。追加而不覆盖，最多500条/周期，超限明确报错，不丢弃旧内容。用户更正时追加“更正哪条、正确内容”，消费时优先解释明确更正，同时保留原始历史。

同 operation_id 重试不重复追加；发布和回读后才称已保存。周期反馈不自动修改各个任务状态，也不覆盖计算报告；明确涉及某条任务实际状态的反馈，再对该任务使用原有生命周期操作。普通删除仍保留 Git 历史。

## 消费与画像

`plans.py --stdin period-notes` 的 envelope.query 为上述 scope。固定 commit 先读取入口，再只请求确定路径；部分输入缺文件返回 needs_files，不能当成没有备注。宿主实际核实该路径不存在后才能显示空备注；complete=true 的完整树可直接确认 absence。返回原文、路径、SHA-256 和 snapshot，保留事实出处。

报告按所需周期读取整体反馈与相关计划的周期反馈，单条备注读计划原文。跨周期分析逐段定向读取；日期窗口统计与用户反馈分开，不把同一段原话在日/周/季度重复计为独立证据。需要完整历史时另查 Git 历史和活动记录；不承诺当前快照能还原所有过去状态。

这些原始反馈可支持 Atlas 的目标、经历、偏好和有时间范围的观察提案，需保留来源范围、用户原话和推测区别。画像越完整依赖长期真实反馈，不意味着从缺失数据或完成率推断人格；Atlas 仍按现有流程只提案、不自动同步。
