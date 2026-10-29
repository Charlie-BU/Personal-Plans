# Personal Plans

**Keep your goals, daily plans, feedback, and recurring routines in files you own.**

[简体中文](README.zh-CN.md) · [Skill instructions](SKILL.md) · [Contributing](CONTRIBUTING.md) · [MIT license](LICENSE)

Personal Plans is an agent skill for turning long-term goals into milestones and daily actions, scheduling time, recording outcomes, and reviewing progress. Your agent accesses a Git repository you choose through its existing tools. The skill owns only `plans/`; it can share a repository with [Personal Atlas](https://github.com/Charlie-BU/Personal-Atlas) and other personal skills.

This repository distributes the skill, **not your personal data**. Instructions are primarily Chinese; project documentation is bilingual.

## What it does

- Connect goals, milestones, tasks, and individual recurring occurrences. Every plan has an explicit four-quadrant priority and a reason.
- Keep scheduled time, deadlines, original deadlines, and actual completion separate. Preserve unknown outcomes rather than treating missing feedback as failure.
- Save notes for individual plans, days, weeks, months, quarters, years, and custom periods. Keep user statements separate from agent observations.
- Retrieve specific records through compact indexes, then verify original content and hashes. No vector database is required.
- Save portable cronjob definitions, scheduler bindings, and execution receipts so another compatible agent can inspect and take over the workflow.
- Offer evidence-based proposals for enriching Atlas. Atlas synchronization is not automatic.

## Example interaction

> Help me finish a portfolio over the next six months. Plan the first milestone this week and schedule project reviews every two days.

> I completed today's review. Save this note: the examples helped, but I need more time for the introduction.

> Review this quarter, including my feedback. Distinguish confirmed completion from unknown outcomes.

Viewing plans, goals, agendas, search results, and period reviews defaults to concise tables, including a notes column; reminders follow the same rule. Explicit format requests take precedence. Empty results are stated directly, and channels without table support use readable cards or structured lines. For example, **fictional data**:

| Time | Plan | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| 09:00–10:00 | Select portfolio examples | Important, not urgent | Pending | Start with three projects |
| Evening | Review one project | Important, not urgent | Time to confirm | — |

A morning reminder both summarizes existing plans and invites you to create or supplement today's plan. A table is an agent response format, not a bundled graphical application.

## Install

Clone into a directory named `personal-plans` in your host's skills folder, or use its skill importer. For a local Codex setup:

```sh
git clone https://github.com/Charlie-BU/Personal-Plans.git ~/.codex/skills/personal-plans
```

Do not overwrite an existing installation without reviewing it. Keep `SKILL.md`, `references/`, `scripts/`, and `agents/` together. The host must support agent skills and provide its own repository, calendar, scheduling, and notification tools as needed; this skill is not an MCP server or a background daemon.

Python 3.9+ is required for the calculation helpers:

```sh
python3 -m pip install -r ~/.codex/skills/personal-plans/scripts/requirements.txt
```

Use a virtual environment when your Python installation requires one. Dependencies are `python-dateutil` for recurrence rules and `tzdata` for environments without a timezone database.

## First use

Tell your agent:

> Use Personal Plans. My personal data repository is `https://github.com/YOUR-NAME/YOUR-PRIVATE-DATA`. Check access and guide me through setup. My timezone is Asia/Shanghai. Start with my portfolio goal.

Onboarding checks your repository, timezone, selected calendar, and first plan. Existing valid data is reused; unrelated directories are preserved. Calendar access is verified through host tools. Missing calendar or scheduler capabilities are reported explicitly and do not prevent discussing or saving plans when repository access is available.

Noncredential connection settings are stored outside the skill at `~/.config/personal-plans/config.json`. Credentials stay in the host's authorized connection. Do not paste tokens into chat or commit them to this repository.

Onboarding explains two defaults, in your timezone:

| Job | Default time | Purpose |
| --- | --- | --- |
| Morning plans | 08:00 | Remind you of existing plans and invite you to establish or supplement today's plan |
| Evening review | 21:00 | Summarize confirmed outcomes, changes, unknowns, and notes |

Either can be disabled or rescheduled. Defaults are saved after the disclosed setup is accepted and a personal channel is authorized. **Saved `enabled` configuration does not mean a scheduler is connected or a notification was delivered.** Hosts must register or reuse the actual scheduler, verify it, and save its binding. Test messages require authorization. Weekly reviews and goal check-ins are optional.

## Storage and retrieval

```text
personal-data/
├── atlas/                         # Another skill's data
├── plans/
│   ├── vault.json
│   ├── goals/                     # Goals and milestones
│   ├── tasks/                     # One-off tasks and recurring templates
│   ├── occurrences/YYYY-MM/       # Persisted exceptions and outcomes
│   ├── periods/                   # Daily and arbitrary-period feedback
│   ├── activities/                # Changes, reasons, and prior states
│   ├── operations/                # Operation deduplication
│   ├── cronjobs/                  # Definitions, bindings, run receipts
│   ├── indexes/catalog/           # Up to 16 condition → ID shards
│   ├── indexes/records/           # Up to 16 unique-card shards
│   ├── manifests/                 # Authoritative content hashes
│   └── reports/                   # Archived reviews and evidence snapshots
└── health/                        # Another skill's data
```

Queries pin one repository commit, route by date/status/goal/keyword, read the needed card shards, and verify original records. Card summaries are navigation, not evidence. Chinese character/bigram and Latin token matching is lexical, not semantic recall. Query results are paginated; a shard can contain navigation metadata for unrelated records, without reading their original content.

Writes validate the complete `plans/` authority, prepare changes in memory, then require a protected atomic Git commit and remote readback. `prepared` means ready to publish, not saved. Whole-repository HEAD protection also accounts for other skills' commits. Helpers do not create a local personal vault, cache, or offline queue.

Recurring templates expand only the requested window. An occurrence is persisted when it needs its own outcome or edit. Editing one occurrence does not rewrite the whole series. Official workday/holiday recurrence needs verified regional data; missing coverage is not guessed.

## Cronjob portability

Definitions describe **when to run and what to do**, not a frozen list of plans. Every run must read the latest remote plans and policies. Adding, updating, completing, or cancelling plans does not require changing the periodic job. Timing, timezone, channel, enablement, or execution behavior changes do.

Takeover checks and pauses the previous scheduler before activating another. Runs use stable trigger identities and remote claims; unknown delivery is reconciled instead of blindly resent. Git and external messaging do not form an exactly-once transaction. Cross-agent portability requires compatible tools, repository access, channel authorization, and access to the previous scheduler; it is not unconditional automatic migration.

## Upgrade to compact indexes

Only `index_format=2` is supported; there is no old-format reader. Existing data repositories need an explicit `reindex` before use. Ask your agent to follow [storage.md](references/storage.md): validate authoritative files, rebuild derived indexes, apply changes atomically, and remotely read back and verify. Plans, feedback, jobs, and sibling directories must remain byte-for-byte unchanged. Updating skill code alone does not migrate data.

## Development

```sh
python3 -B -m unittest discover -s scripts -p 'test_*.py' -v
python3 -B scripts/demo.py
```

The demo and tests use synthetic in-memory repositories. They do not connect accounts, write personal data, register schedulers, or send messages. CI runs both across Python versions. Local contracts do not prove live integration for every host or provider.

See [storage](references/storage.md), [retrieval](references/retrieval.md), [cronjobs](references/cronjobs.md), [period feedback](references/period-notes.md), and [reports](references/reports.md) for the operational contracts.

## Privacy and license

Use a private personal-data repository and grant only required access. Do not include real plans, credentials, notification receipts, or private screenshots in public issues. Ordinary deletion does not erase Git history or external calendar/message copies. Shared repositories do not automatically authorize cross-skill access. Personal feedback can support Atlas proposals; completion statistics do not justify personality judgments.

Released under [MIT](LICENSE). Dependency notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Contributions in Chinese or English are welcome.
