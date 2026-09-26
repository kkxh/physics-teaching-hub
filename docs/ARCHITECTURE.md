# 架构说明

面向想读代码或提 PR 的人。三段话讲完：**配置与文案在外层、数据访问只有一个入口、
领域模块之间不互相调用、输出层（报告 / 看板 / API）只读不写。**

## 1. 分层

```
        ┌─────────────────────────── CLI 层 ───────────────────────────┐
        │  hub.py（唯一入口，子命令分发）  init_db.py / import_scores.py │
        │                                  make_report.py（Phase 1 垫片）│
        └───────────────┬───────────────────────────────┬──────────────┘
                        │                               │
        ┌───────────────▼─────────────┐   ┌─────────────▼──────────────┐
        │ 领域模块（各管一摊，互不调用） │   │ 输出层（只读，不写业务表）   │
        │ importer.py    导入框架       │   │ reports.py    周报 / 巡检   │
        │ homework.py    作业与订正     │   │ dashboard.py  单文件看板    │
        │ errors.py      错因与行为     │   │ api.py        本地只读 API  │
        │ exam.py        考试与讲义     │   │ make_report.py 最小闭环报告 │
        │ profiling.py   学生画像       │   │ exam.py 的讲义渲染同属输出层 │
        │ alerts.py      预警与跟进     │   └─────────────┬──────────────┘
        │ questions.py   题库           │                 │
        └───────────────┬─────────────┘                 │
                        │                               │
        ┌───────────────▼───────────────────────────────▼──────────────┐
        │ db.py：唯一的连接入口（PRAGMA foreign_keys=ON、schema 版本校验）│
        │ schema/*.sql（新库） + schema/migrations/*.sql（存量库升级）   │
        └───────────────────────────────┬──────────────────────────────┘
                                        │
        ┌───────────────────────────────▼──────────────────────────────┐
        │ config_loader.py（配置 + 路径 + 时区）  labels.py（文案表）     │
        │ seed_demo_data.py（虚构演示数据，被演示/测试共用）              │
        └──────────────────────────────────────────────────────────────┘
```

## 2. 模块职责与依赖

只列本仓库内部的 import（标准库与 `openpyxl` 略）。

| 模块 | 依赖 | 职责 |
| --- | --- | --- |
| `config_loader.py` | `labels`、`stage_profiles` | 读 `config.toml` / 环境变量白名单，解析路径、学期、课表、题库白名单；对外给 `AppConfig` |
| `labels.py` | — | 加载 `labels/<locale>.toml`，缺 key 直接报错 |
| `stage_profiles.py` | `labels` | 学段的默认教学阶段模板 |
| `teaching_calendar.py` | `config_loader` | 教学日 / 教学周 / 当前阶段（按配置时区） |
| `db.py` | `config_loader` | **唯一**开连接的地方；schema 版本校验、表存在性判断 |
| `importer.py` | `config_loader`、`db` | 通用导入框架：学生匹配（ID 优先）、考试匹配、CSV/Excel 解析、`LIKE` 转义 |
| `homework.py` | `importer`、`seed_demo_data`、`db` | 作业提交与订正、完成率口径（只算本班当前学生） |
| `errors.py` | `importer`、`db` | 错因标签字典、错因与行为记录（预览 → 确认 → 单事务） |
| `exam.py` | `importer`、`questions`、`db` | 小题级分析（得分率 / 难度 / 区分度）、讲评讲义、**题库组卷讲义渲染** |
| `profiling.py` | `db` | 三个维度的纯函数算分 + 增量落库 |
| `alerts.py` | `db` | 规则扫描（幂等）、跟进闭环 |
| `questions.py` | `importer`、`seed_demo_data`、`db` | 题目 schema 之上的读写原语、导入器、检索、推荐 |
| `reports.py` | `alerts`、`homework`、`teaching_calendar`、`db` | 周报与阶段巡检（现算，不落库） |
| `dashboard.py` | `api`、`db`、`labels` | 构建时把数据嵌进单文件 HTML |
| `api.py` | 若干领域模块、`db` | 本地只读 GET，绑定 `127.0.0.1`，CORS 白名单 |
| `hub.py` | 几乎全部 | 参数解析 + 配置装载 + 分发；业务逻辑都在各模块里 |
| `seed_demo_data.py` | `errors`、`questions`（函数内） | 生成确定性的虚构数据集 |
| `init_db.py` | `db`、`seed_demo_data`、`errors`、`homework` | 建库、应用迁移、导入演示名单；`upgrade-db` 的实现 |

两条容易踩的依赖约定：

1. **连接只从 `db.py` 走**：除 `db.py` 外任何模块出现 `sqlite3.connect(` 都会被守护测试打回
   （`tests/test_db.py::test_only_db_module_opens_connections`）。
2. **`questions.py` ↔ `seed_demo_data.py` 是单向的**：`questions` 在模块级 import `seed_demo_data`
   （`--demo` 要读演示数据集），反过来 `seed_demo_data` 只在函数内 import `questions`
   （要复用示例题集）。改这里时别把函数内 import 提到模块级，会成环。

## 3. 数据流

**成绩 → 分析 → 报告**
`import-scores`（CSV/Excel → `importer` → `exams` / `exam_scores`）
→ `import-item-scores`（`exam_items` / `item_scores`）
→ `exam-analysis` / `make-handout`（`exam.py` 现算）
→ `weekly-report` / `phase-patrol`（`reports.py` 现算）。

**记录 → 画像 → 预警 → 跟进**
`record-error` / `record-behavior`（`errors.py`，预览后单事务写入）
→ `compute-profile`（`profiling.py` 物化到 `ability_scores`，默认只算源数据变过的学生）
→ `scan-alerts`（`alerts.py` 幂等扫描）→ `resolve-alert` 必须留跟进记录才关闭。

**题库 → 检索 / 推荐 → 讲义**
`import-questions`（JSON / CSV / `--demo`，整批单事务、标签白名单）
→ `list-questions`（只读，答案默认隐藏；API 用 `as_public_dict`，不带答案与解析）
→ `recommend-questions`（错因标签经 `[question_bank.tag_map]` 映射到知识点 × 画像难度档，纯只读）
→ `make-handout --question-keys / --recommend-for`（渲染成 Markdown，默认不含答案）。

**出站产物**：都在 `config.paths.output_dir`（默认 `outputs/`），只写相对路径，可整包拷走。

## 4. schema 与版本演进

- 新库：`init_db.py` 按固定顺序执行 `schema/*.sql`（`core → scores → homework → errors → profile → alerts → questions`），
  再记录随库分发的迁移，最后写 `meta.schema_version`。
- 存量库：已知旧版本（`db.MIGRATABLE_SCHEMA_VERSIONS`，例如 `phase2`）走
  `schema/migrations/*.sql` + `hub.py upgrade-db`——**只跑迁移、校验表齐全、再写版本号，不删库**；
  `phase1-temp` 与未知版本只给重建这一条路。
- 迁移必须幂等可重放；`schema/questions.sql` 与 `schema/migrations/0001_questions.sql` 要保持一致
  （有测试断言新库与迁移后的老库结构相同）。

## 5. 配置与文案

- `config_loader.py` 是配置的唯一入口：文件 → 白名单环境变量 → 默认值；
  相对路径一律相对**配置文件所在目录**解析。
- 面向使用者的教学文字在 `labels/zh-CN.toml`，代码里只出现 key
  （守护测试会检查生产模块没有硬编码学科/学段字样）。
- 学段决定默认阶段与部分术语；题库的知识点标签白名单在 `[question_bank] tags`，
  错因标签扩展在 `[error_tags]`——两套词典分开。

## 6. 隐私边界（架构层面的硬约束）

- **进仓库的**：代码、`schema/`、`labels/`、`config.example.toml`、`docs/`、
  `examples/`（自制示例题）、测试。
- **不进仓库的**：数据库、导入文件、报告与看板产物、演示数据集、`config.toml`、
  真实学生/班级/学校信息、第三方试卷与教辅内容（`.gitignore` + `docs/PRIVACY.md`）。
- 校验手段：`scripts/privacy_scan.sh` 四个模式——默认（Git 跟踪）、`--all`（可能被提交的）、
  `--strict`（整个工作区）、`--history`（全部提交与历史文件版本，发布前硬性动作）。
- 出站接口不返回答案与解析；推荐是只读的（有行数不变断言）。

## 7. 加一个新模块时的清单

1. 业务逻辑放模块里，`hub.py` 只加子命令与打印（薄分发层）；
2. 连接从 `db.connect` 拿，函数接受显式连接或 `db_path`；
3. 写入走「预览 → `--yes` → 单事务」，dry-run 与执行共用同一份计划；
4. 用户可见文字进 `labels/zh-CN.toml`，配置项进 `config_loader.py` + `config.example.toml` + README 配置表；
5. 新模块名加进 `tests/test_labels.py` 的扫描清单；schema 变更走 `schema/migrations/`；
6. 补测试（主路径 + 至少一个边界），跑四条绿灯与 `privacy_scan.sh --all`；
7. 更新 `docs/MIGRATION_LEDGER.md`（状态、日期、与上游的差异）。
