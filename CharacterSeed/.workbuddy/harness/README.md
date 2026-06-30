# AI 开发 Harness 保护系统

一套动态约束 + 验证 + 持续迭代的 AI 开发保护系统，用于防止 AI 在开发过程中破坏现有可用功能。

## 方法论

将传统"文档驱动"升级为"harness 驱动"：

| 维度 | 文档驱动 | Harness 驱动 |
|------|----------|--------------|
| 形态 | 静态规范文档 | 动态执行的保护系统 |
| 作用 | 描述 AI 应做什么 | 验证 AI 实际做得对不对 |
| 周期 | 一次性 | 持续迭代优化 |
| 失效 | 文档过时即失效 | 规则与代码同步演进 |

## 核心模块

```
.workbuddy/harness/
├── harness.py              # 统一 CLI 入口（编排所有模块）
├── pre_check.py            # 操作前预检（6 道闸门）
├── change_logger.py        # 变更日志 + 哈希链 + 受控回滚
├── regression_runner.py    # 回归验证 + 失败自动阻断/回滚
├── approval.py             # 核心逻辑修改审批工作流
│
├── protection_rules.json   # 保护约束规则（边界+质量门禁+反馈循环）
├── feature_registry.json   # 已完成功能注册表（13 个 feature）
├── core_lock.json          # 核心逻辑锁定清单（8 个模块）
├── pitfall_registry.json   # 重复踩坑+难排查登记表（15 条）
│
├── approvals.jsonl         # 审批记录（运行时生成）
└── logs/
    ├── change_log.jsonl    # 变更日志（含哈希链）
    ├── incidents.jsonl     # harness 事件（回滚/阻断/失败）
    ├── reviews.jsonl       # 反馈循环审查记录
    ├── diffs/              # 每次变更的 diff 快照
    └── report_*.md         # 生成的 Markdown 报告
```

## 四类优先保护内容

| 类别 | 来源 | 策略 |
|------|------|------|
| 01 旧功能 | `feature_registry.json` | 修改前预检 + 关联测试回归 |
| 02 核心流程 | `core_lock.json` | 任何修改须走 `approval.py` 审批 |
| 03 重复踩坑 | `pitfall_registry.json` (frequency≥2) | purpose 必须引用 PIT-XXX ID + guard_tests |
| 04 难排查 | `pitfall_registry.json` (difficulty=hard) | 额外回归覆盖 + 详细 rationale |

## 6 道预检闸门

`pre_check.py` 在 AI 修改代码 *之前* 执行：

1. **read_only** — 是否触碰只读文件（critical 阻断）
2. **core_lock** — 是否触碰核心锁定模块（critical 阻断，需 approval 解锁）
3. **feature_registry** — 是否影响已完成 feature（要求跑关联测试）
4. **pitfall** — 是否命中已知踩坑点（purpose 必须引用 PIT-XXX ID）
5. **danger_patterns** — 是否引入禁止代码模式（DROP TABLE / git push --force 等）
6. **conflict_rules** — 是否触发跨模块冲突规则

## 质量门禁（quality_gates）

`regression_runner.py` 在 AI 修改代码 *之后* 执行：

| 门禁 | 描述 | 阻断 |
|------|------|------|
| functional | 受影响 feature 关联测试 100% 通过 | 是 |
| regression | 全量 pytest 通过率 ≥ 95% 且无新增失败 | 是 |
| no_new_pitfall | 不引入已知 pitfall 模式 | 是 |
| lint_clean | py_compile 全部变更文件 | 是 |
| build_ok | 前端变更须 vite build 通过 | 是（仅 web/src 改动时触发） |

## 使用方式

### 典型 AI 开发流程

```bash
cd CharacterSeed

# 1. 修改前预检
python .workbuddy/harness/harness.py pre-check \
    --purpose "修复 Jiwen tick 死循环 (PIT-007)" \
    --files backend/jiwen/jiwen_core.py

# 2a. 如果预检通过 → AI 修改代码 → 后置回归验证
python .workbuddy/harness/harness.py post-verify \
    --files backend/jiwen/jiwen_core.py \
    --before-hash abc1234 \
    --required-tests tests/test_jiwen_core.py

# 2b. 如果预检 BLOCKED（核心锁定）→ 走审批
python .workbuddy/harness/approval.py request \
    --lock-id LOCK-003 \
    --purpose "修复 tick 死循环" \
    --files backend/jiwen/jiwen_core.py \
    --changeset "修改 JiwenEngine.tick 的循环退出条件"

# 人工批准
python .workbuddy/harness/approval.py approve \
    --id APP-20260629_223000_abc123 \
    --reviewer 张三 \
    --note "已确认安全"

# 2c. 紧急通道（24h 临时授权）
python .workbuddy/harness/approval.py emergency \
    --lock-id LOCK-003 \
    --purpose "生产事故修复" \
    --files backend/jiwen/jiwen_core.py \
    --reviewer 值班工程师 \
    --ttl-hours 24
```

### 一键受控执行

```bash
# pre-check → 执行命令 → 自动回归验证
python .workbuddy/harness/harness.py run \
    --purpose "新增 character memory API (PIT-012 已确认)" \
    --files backend/api/character_memory_router.py \
    -- python -m pytest tests/test_character_memory_router.py
```

### 总览与报告

```bash
# 系统总览
python .workbuddy/harness/harness.py status

# 生成 Markdown 报告
python .workbuddy/harness/harness.py report

# 反馈循环：扫描 incidents 提建议
python .workbuddy/harness/harness.py review

# 校验日志哈希链完整性
python .workbuddy/harness/harness.py verify-chain
```

### 回滚

```bash
# 预览
python .workbuddy/harness/harness.py rollback --to abc1234 --dry-run

# 实际回滚（必须提供 reason，会保存现场 diff）
python .workbuddy/harness/harness.py rollback --to abc1234 --reason "回归测试失败，回滚到稳定版本"
```

## 边界定义

| 级别 | 范围 | 策略 |
|------|------|------|
| read_only | `backend/services/db_migration.py`, `backend/models.py` | 完全禁止，须审批 |
| restricted | `backend/{jiwen,modules,services,api,crud,world,memory}/` | 须预检 + 回归 |
| sandbox | `tests/`, `web/src/`, `.workbuddy/`, `docs/`, `data/` | 自由修改 |

## 行为约束（AI 必须遵守）

1. **修改前必须 pre-check**：`python .workbuddy/harness/harness.py pre-check ...`
2. **核心修改必须 approval**：触碰 `core_lock.json` 的文件前必须获得审批 ID
3. **踩坑点必须引用**：触碰 `pitfall_registry.json` 的文件时 purpose 必须含 `PIT-XXX`
4. **变更后必须 post-verify**：所有变更必须跑回归，failure 自动回滚
5. **回滚必须走 harness**：禁止裸 `git reset --hard`，必须 `harness.py rollback --reason ...`
6. **日志不可篡改**：change_log 含哈希链，`verify-chain` 可检测任何篡改

## 反馈循环

- 每次 incident（阻断/回滚/失败）记录到 `logs/incidents.jsonl`
- 每周运行 `harness.py review` 扫描 incidents 提出规则更新建议
- 同一 pitfall 命中 ≥3 次或 critical 事件触发规则迭代
- 新发现的踩坑点应及时登记到 `pitfall_registry.json`

## 与现有开发流程集成

- **pre-commit hook**（建议）：在 `.git/hooks/pre-commit` 调用 `harness.py pre-check`
- **CI**（建议）：在 CI 流水线增加 `harness.py post-verify --full` 步骤
- **code review**：审阅 PR 时附 `harness.py report` 输出
