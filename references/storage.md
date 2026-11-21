# 共享仓库与连接契约

Skill 与数据分开：技能目录只安装代码和说明；个人资料的权威来源是用户指定远端仓库的 plans/。已有授权 Git 工作区可复用。连接配置可用上下文、上层设置或可选 ~/.config/personal-plans/config.json；仓库、分支、凭据均不写死在通用 Skill。没有本机配置文件不阻塞已有授权上下文。

```text
共享仓库/
  atlas/                         # 不属于本 Skill
  health/                        # 不属于本 Skill
  plans/
    vault.json                   # schema/id/revision/timezone/calendar连接状态
    goals/<id>.json              # 目标与里程碑
    tasks/<id>.json              # 单次任务/重复模板
    occurrences/YYYY-MM/<id>.json # 按原始实例日期归档，改期不换身份
    activities/YYYY-MM/<op>_<n>.json # 变更、原因、时间、操作者
    calendars/holidays/<id>/<year>.json
    policies/onboarding.json
    policies/reminders.json
    integrations/calendar/<hash>.json
    integrations/notification/<hash>.json
    cronjobs/definitions/<id>.json # 可迁移定时任务定义
    cronjobs/bindings/<id>.json    # 当前调度器与定义修订绑定
    cronjobs/runs/YYYY-MM/<hash>.json # 运行领取与结果回执
    indexes/cronjobs/<page>.json  # 定时定义和绑定的路径/哈希
    periods/<kind>/<start-year>/<scope-hash>.json # 每日及任意周期的原始反馈
    indexes/root.json
    indexes/catalog/<1hex>.json  # 条件→ID列表，最多16片
    indexes/records/<1hex>.json  # ID→唯一导航卡片，最多16片
    indexes/effects/<state>/YYYY-MM/<page>.json
    manifests/files.json         # 所有权威文件的SHA-256；不含派生索引
    reports/YYYY-MM/<report_id>.json # 指标、叙述和证据快照
    operations/<operation_id>.json
```

仓库可空可非空；只检查 plans/。缺失或空目录在用户接受建库流程后初始化；已有有效目录复用 ID；文件占位、陌生非空内容、读取失败和不完整清单均不初始化。不要自动迁移其他目录或覆盖根 README/.gitignore。

## 复用已有 Git 工作区

安装完整技能目录，包含 `SKILL.md`、`references/` 和 `scripts/`。优先复用已验证的 Python 环境；新建环境优先 Python 3.14，宿主不支持时选择其他已验证版本。在该环境中一次安装 `scripts/requirements.txt`，用同一版本的脚本和说明。使用宿主已有 Git 认证和用户授权的工作区。这个入口不会克隆数据仓库，也无需额外建立应用缓存。

先在工作区用宿主已有能力同步目标分支，取实际 commit 并在本次操作中固定。工作区必须已有该 commit 的 Git 对象；普通读取也可使用用户指定的历史 commit。Git 快照读取忽略工作树和暂存区，不会切换分支。下例中的 `$repo`、`$commit`、`$branch` 取用户已确认的目标，`$skill` 指向完整技能目录。

```bash
python3 -B "$skill/scripts/git_plans.py" --repo "$repo" --ref "$commit" status </dev/null
python3 -B "$skill/scripts/git_plans.py" --repo "$repo" --ref "$commit" query <query.json
python3 -B "$skill/scripts/git_plans.py" --repo "$repo" --ref "$commit" apply <request.json
python3 -B "$skill/scripts/git_plans.py" --repo "$repo" --ref "$commit" \
  --publish origin "$branch" apply <request.json
```

输入与原脚本的 `query`、`plan`、`request`、`settings` 字段一致；Git 入口负责快照、文件和清单。写入的 `base_commit` 保留在原请求要求的位置。示例中的 JSON 是宿主提供的标准输入，也可直接通过管道传入。读取自动补齐单页所需的文件；需要完整结果时按 `next_offset` 续页，并始终使用同一 commit。执行写入时才批量读取完整的 `plans/`，整批兼容变更可使用原有 `apply` 的 `changes`。未使用 `--publish` 时只准备差异。已有分支的首个 `plans/` 初始化可走此入口；空仓库首次建分支走宿主的首提交流程。

显式发布只使用已配置且读写 URL 相同的单一 Git remote。先检查远端分支，再生成以校验基准为唯一父提交的候选，用绑定该父提交的 lease 更新分支。即使分支在检查后被回退，lease 也拒绝写入；候选只允许快进。发布后回读远端分支的提交哈希，哈希相等确认了整个树与已构造的候选一致。需要展示保存内容时，按此提交定向回读受影响记录和操作回执。若 Git 返回 `publication_unconfirmed`，保存候选 commit 和原操作 ID。先核对候选是否在当前远端分支的祖先链，以及回执和记录是否正确，再决定是否重做。不要用新的操作 ID 重试未知结果。

Git 工作区缺失、无法访问或不能同步目标分支时，说明具体原因，提醒用户配置或修复授权工作区，然后使用可用的连接器按固定快照执行原有标准输入协议。连接器写入仍须完整校验、原子发布并回读确认。单次写入优先采用宿主可用的范围限定批量传输；缺少批量路径时要明确逐文件读取成本。避免把完整快照或大段 base64 传进模型上下文。

## 读取

1. 发现与明确仓库/身份匹配的 Git/连接器工具，读取元数据和分支 HEAD。
2. 固定该 commit 按需取 plans/ 文件，查询按 retrieval.md 递进。
3. 入口成功是 entrypoint_only，不表示全文已查或日历可写。
4. 完整 verify/write 必须确认 plans/ 清单完整，传 complete=true，缺文件/哈希变更/索引不符则停止准备；不能为了通过校验把缺失文件当删除。

配置、权限、网络、目录不存在分别报告。404不证明空库；PAT 本身不代表已有客户端。缺权限引导宿主授权，不自造远端客户端兜底。

## 原子写入

完整读取 plans/ 子树，在内存通过辅助脚本准备。输出文件和删除路径始终仓库相对、带 plans/ 前缀。以仓库原有树为基础仅应用这些局部变化，禁止用 plans/ 内容替换整棵树。所有权威记录、活动、索引、manifest、操作去重同一次 commit 发布，非强制更新分支。

base_commit 是整个仓库 HEAD。如果另一 Skill 推进了 HEAD，从最新提交重新准备并核对原操作是否已存在。Git 工作区可复用已有对象，重新准备仍校验该提交的完整 plans/。业务 record 更新还需 expected_sha256。新操作生成唯一稳定 operation_id，响应丢失先查原操作和候选提交，不用新 ID 盲目重试。

init 和普通操作返回 prepared、remote_confirmed=false，只说明校验和准备完成。工具提交后，回读实际 commit、vault/index revision、记录和相应 operation_id；HEAD 已前进时验证候选提交在分支祖先中且内容正确。只回读任意新 HEAD 不算确认。

第一次空仓库的首个多文件提交能力按实际工具检查。仅支持逐文件独立提交的工具不能安全发布；允许查询和准备，但必须说明写入未完成。没有 Python 时仍可读索引；写入需要受信任的等价完整校验能力。

## 日历和提醒

Git 与外部服务不是一个原子事务。先保存任务事实与待执行意图，再由现有工具处理外部动作，实际回读后记录 receipt；失败/未知保留远端状态并核查。外部能力依赖宿主，Skill 没有自带账号、后台执行器或消息服务。

凭据、token 和真实个人数据不进入技能源码、测试或示例；本机可选配置权限按宿主规范保存。结构中的 calendar_id/channel 是非凭据引用，认证留在连接器。普通删除保留 Git 历史，另行处理彻底清除的范围。

## 重建派生索引

只支持 index_format=2，没有旧格式生成器或兼容读取器。升级已有资料库时，宿主完整读取 plans/，固定实际 HEAD，使用 `plans.py --stdin reindex`。该操作校验 vault/root 身份、修订一致性、manifest 中全部权威文件哈希、记录和关系，然后只重建 indexes/ 与 manifests/，删除不再使用的派生路径；不修改计划、备注、周期反馈、cronjob、回执、报告或其他目录。坏索引可由原始数据重建，原始文件缺失、哈希不符或清单不完整则拒绝，不能靠重建吞掉数据损失。

结果仍是 prepared。核对 delete_paths 全为派生路径、原始文件字节不变，以现有整仓库树为基础原子提交并非强制更新分支，回读 index_format=2 并 verify 后才报告完成。并发 HEAD 变化重新准备。reindex 不追加业务操作记录，不改 vault 修订；以提交、root 格式和 verify 回读确认结果。新格式无需变更时输出空差异。仅更新本地 Skill 不会自动改写远端索引。

reindex 的完整性例外：宿主核对完整 Git 子树清单后，可只读取全部权威文件、manifest 和旧 root 原文，并在信封 index_paths 提供完整的 indexes/、manifests/ 文件路径清单（固定同一提交）。此时 complete=true 表示权威原文与路径清单完整，旧派生文件正文可省略；脚本仍严格比对全部权威文件哈希，再从路径清单计算废弃索引删除。不得将缺失权威原文当作可省略的索引，也不能用此例外执行普通写入或 verify。
