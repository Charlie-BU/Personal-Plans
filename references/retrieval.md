# 精确检索：结构约束 → 分片索引 → 原文证据

每日及周期反馈按 [period-notes.md](period-notes.md) 的类型、起止日期、时区和可选 plan_id 直接计算稳定路径，用 period-notes 读取原文；无需扫描整库或新增索引。单条计划备注随 read 原文取得。相关日期和周期须由问题及计划设计明确，不把不同周期或不同计划的反馈混为一谈。

定时任务用 cronjobs 命令：root.cronjob_pages → indexes/cronjobs/<page>.json → 定义/绑定原文及哈希，支持 needs_files 递进补读。不加载运行历史；单次运行按 [cronjobs.md](cronjobs.md) 的稳定键定位。旧库缺该字段表示尚无定时任务。

## 存储选择

每个目标/任务/实例独立 JSON：路径稳定，避免整库 Markdown 或单个大 JSON。索引是确定性派生、可重建的查找结构；不作为第二份可编辑事实。层级、依赖、跨命名空间引用只建立必要邻接入口，不先引入全局知识图或向量。

先读 plans/vault.json、plans/indexes/root.json，核对 schema、vault_id、revision，并固定同一仓库 commit。root 只含版本、数量、路由方式、存在的 catalog 分片和外部动作月份入口，不嵌入全量记录。写入验证另用完整 manifest；定向查询不需先读全库 manifest。

只支持 root.index_format=2；不保留旧格式读写。索引是最多32个固定分片，不再按检索词创建目录：

1. `plans/indexes/catalog/<1hex>.json`：`sha256(route)` 首位决定分片。每个 route（日期、状态、父项、关键词等）只保存有序计划 ID 列表。
2. `plans/indexes/records/<1hex>.json`：`sha256(id)` 首位决定分片，ID → 唯一卡片。卡片含路径、SHA-256、标题、摘要预览和必要元数据；每条记录只保存一份，不在各条件下重复复制。
3. 按命中 ID 取卡片，再读取原文并校验内容哈希。一个分片可含其他记录的导航信息，不代表读取了它们的原文。

root.catalog_shards_present 不含分片，表示该快照没有相应路由；存在但未取到则返回 needs_files，不能当作无命中。分片必须取得完整内容，不得将连接器截断当成完整；本方案降低文件数，但数据持续增长时单分片也会增大。查询结果仍用 offset/limit 分页，外部动作、报告和 cronjob 索引仍按原有分页，二者不要混淆。

## 支持的查询与路由

| query 字段 | route | 用途 |
|---|---|---|
| id | id:ID | 精确单条 |
| kind | kind:goal/milestone/task/occurrence | 类型范围 |
| status | status:pending 等 | 生命周期 |
| quadrant | quadrant:important_not_urgent 等 | 四象限 |
| parent_id | parent:ID | 直接子项；逐层查询形成目标树 |
| dependent_of | dependent:ID | 哪些记录依赖它 |
| reference | ref:NAMESPACE:ID | 例如引用某 Atlas 项目的计划 |
| on | day:YYYY-MM-DD | 已落库的安排/期限，按 vault 时区；重复视图用 agenda |
| original_on | original-day:YYYY-MM-DD | 原始期限，避免延期后失去检索入口 |
| text | term:词元 | 原文、标题、摘要、别名和标签 |

指定 id 时仅定位该条索引并核验原文中的其他条件，不为宽泛状态/类型额外读取其他分片。其他多个确定字段取交集。text 可为一个短语或最多12个显式别名/表述，短语间取并集；每个短语先用一个必要词元定位候选，候选过多时才补取其他词元求交集，再回读候选正文核验连续短语。中文单字/二元组支持具体短语定位，拉丁字母采用规范化词元，不是语义模型，也不保证英文词中间子串匹配。没有命中时试用户别名、同义表达、目标/领域上下文，或枚举相关结构范围，不能把一次关键词空结果当事实不存在。

一条记录如果有多种称呼，落库时保留 aliases；优先写“主题清晰、对象明确、日期和来源可解释”的 summary，而非泛泛“这是一个重要计划”。标签可帮助语义表述扩展，但不代替原文。

## 脚本递进协议

`plans.py --stdin query` 的 JSON 输入见 operations.md。可以从 files_base64={}、固定 snapshot 开始：

- status=needs_files：按 paths 精确补读同一 commit，把已有和新文件在内存合并，重新调用同一命令。读取失败停止相应范围，不初始化、不切换快照。
- status=refine_query：词法候选超过默认60条（max_candidates 可显式设1–200），先加日期、类型、状态、上级目标或更具体表述。不会为了装进预算而静默省略候选。
- status=ok：query 返回 indexed_candidates 和分页；按结果 ID 执行 read，只有 verification=record_hash 的记录才作为最终事实证据。纯计数需要说明对象/筛选口径。

结构过滤不必读未命中正文。词法核验需要读所限定候选的正文，但脚本不回显全部正文；Agent 回答所引用的记录仍应实际 read。没有 Python 时按相同索引和哈希协议使用受信任工具，不能把索引摘要当完整详情。

query 示例：

```json
{"kind":"task","status":"pending","quadrant":"important_not_urgent","parent_id":"goal_language","text":["口语练习","英语表达"],"offset":0,"limit":20}
```

## 日期、每日和长期视图

每日/本周请用 `agenda --from 日期 --to 日期 --as-of 带偏移时间戳`；默认按 vault 时区解释日期。它只读日期索引、重复系列以及相关原始月份的例外，再按规则生成有限实例；已落库例外优先，包括改期移出窗口、取消和跳过。跨午夜与其他时区须按实际重叠匹配。--carry-over 可加入既有未完成逾期记录，不声称自动发现所有未物化历史重复实例。

单次查询窗口最多366天跨度，跨年长报告分窗处理并按 ID 去重。长期目标本身没有此周期限制，用 goal/milestone 与 parent 路由检索；不要把多年理想建成一个连续日历时间块。尚未排程、无期限的 task 通过状态/上级索引呈现，不会凭空塞入“今天”。

## 关系、完整性和证据

已知子项只读 parent:ID；依赖反向查 dependent:ID。想解释“为什么受阻”，先读任务再沿 depends_on 读取依赖，不遍历整个人生图。完整目标进度必须读尽相关分页和层级，不将只看一层当完整。查询预算到达时说明已读和未读，不回报无条件100%进度。

引用计划 ID、实际 plans/... 路径与固定 commit；外链由宿主生成，不拼入凭据。备注、延期原因、原始期限、completed_at 精度在原文；审计在 activities，Git 旧版本需通过历史工具读取。没有相应证据时标记未知，不以当前状态推断当时状态。

## 已保存报告

root.report_routes 按保存月份列页数；读取 plans/indexes/reports/YYYY-MM/<page>.json 获取报告卡片。saved-reports 返回 path/sha256/source_snapshot；按卡片读取并校验报告原文，报告引用的事实仍以 source_snapshot 为准。
