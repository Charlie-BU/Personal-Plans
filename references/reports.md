# 复盘、建议与证据

复盘前按 [period-notes.md](period-notes.md) 读取本期反馈和相关计划 notes；周期类型包括日、周、月、季度、年和计划自定义周期。报告为用户补充保留“备注/补充”位置。用户完成反馈与补充说明是独立原始资产，必须按其对象落库；不得只并入 Agent narrative 而丢失原话、来源和适用范围。未反馈标为未知，不能用生成的报告代替用户自述。更细或更粗周期的反馈只在明确有关时按范围继续读，不能自动认为本周反馈等于每天反馈。

日报回答今天完成/逾期/调整了什么及明天承接。周报增加重要不紧急任务、目标覆盖、阻塞和重复计划。长期复盘沿目标层级读完整相关范围，说明哪些阶段有行动、哪些待明确，不强迫理想量化。

## 固定口径

report 的 from/to 是 vault 时区的日期窗口，as_of 是观察时刻，snapshot 是固定资料版本。

- 以叶子 task/occurrence 计数，父任务不重复计；重复模板不进入次数分母。
- 当前期限在窗口且到观察时刻已到期才进入当前期限分母；未来实例不算未完成。
- cancelled/skipped 单列，不与逾期混用；没有完成反馈写 unconfirmed，不断言用户没有做。
- 日期级完成碰到同日精确deadline，及时性为 unknown_precision；不能凑成按时完成。
- 无due任务单列；不能用未明确的 time_window 当硬期限。
- 原始期限有独立 original-day 索引和原始期限分母；延期移出本期仍能看见最初承诺。当前完成率与原始承诺及时率不要互相冒充。
- 已删除记录不在当前记录分母；解释完整历史须查 activities 和历史快照。当前报告不是自动重建“当时状态”。as_of 早于该快照的最后修改时刻会拒绝，应改用真实历史快照。

输出包含 due_count、completed_count、completion_rate（无分母则null）、on_time、late、unknown_completion_precision、original_due_count、original_completed_count、original_deadline_on_time、unconfirmed、excluded_count、without_deadline 和 evidence。保存前先解释人类可读口径与差异。

## 建议

优先提出可验证的小调整：例如缩小任务、增加缓冲、明确依赖、保护重要不紧急时间。结合用户授权的其他 Skill 数据时，用 namespace/ID/固定快照引用，不自动扫描health或改写Atlas。健康数据没有授权就注明未使用。

区分事实、推断和建议。不要仅凭完成率下人格结论，不把用户延期推断为懒惰，也不将Agent建议自动改成用户目标/期限。当前版本没有实际工时日志，不能声称完成了预计与实际耗时对比；可先询问并说明需要新增可靠数据字段。

## 交付

默认在对话交付报告，不自动发消息或定时运行。需要持久保存时，在用户授权范围内用 save-report 将计算口径、证据快照和 narrative 同提交存入 plans/reports/，回读后确认；用 saved-reports 按保存日期找卡片并读原文。用户希望周期报告时按 reminders.md 配置真实调度和本人渠道。

完整长期复盘可分窗口和目标层级读取，按ID去重并穷尽分页；上下文不足时说明剩余范围。摘要不能替代被引用记录的read，报告不能替代原始资产。

当前已暂停/取消系列的过去虚拟安排需读取相应历史快照复盘；不要只凭当前模板状态计算过去完整的承诺总数。
