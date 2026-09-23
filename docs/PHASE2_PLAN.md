# Phase 2 详细计划：逐模块搬运

> 状态：**执行中（2026-09-23 起）**——维护者已确认按本计划推进；评审意见异步给，不阻塞开发。
> 本文件在维护者提供的计划稿基础上，按仓库现状与工程约定做了修订，改动见文末「修订记录」。
> 范围锚点：[ROADMAP.md](ROADMAP.md) 的 Phase 2。本文件只细化步骤，不扩大范围；
> 题库属 Phase 3，不在本计划内。
>
> 上游指作者的私有教学系统。本文件及本仓库任何文件**不记录上游的本地路径**
> （[AGENTS.md](../AGENTS.md) 硬规则第 6 条）；执行时先读上游对应模块的实现，再用通用写法重写。

---

## 0. 给执行者（Codex）的一页纸

### 开工前必读（按顺序）

1. `docs/ROADMAP.md` —— 确认 Phase 2 在路线中的位置
2. `docs/MIGRATION_LEDGER.md` —— 台账现状与「预吸收清单」
3. `docs/PRIVACY.md` —— 隐私与版权边界
4. `docs/ENGINEERING_NOTES.md` —— 10 条工程约定，写代码时默认遵守
5. 本文件 —— 按模块顺序执行

### 分支与提交
- 一个模块一个分支：`codex/phase2-模块名`（如 `codex/phase2-scores-import`）；
- 分支内按逻辑内聚拆 2~4 个提交（不要一个模块一个巨型提交，也不要碎到每行一个提交）；
- 提交信息格式：`P2.x 做了什么；与上游的差异：一句话`；
- 每个模块完成后更新 `docs/MIGRATION_LEDGER.md`（状态、日期、与上游的差异），
  差异要写「为什么不一样」，不要只写「不一样」。

### 绿灯命令（每提交前必跑，全绿才提交）

```bash
python3 -m compileall -q .
python3 -m unittest discover -s tests -t .
python3 seed_demo_data.py --check
bash scripts/privacy_scan.sh
```

### 硬规则重申（违反即打回）

1. 只读上游实现，**用通用写法重写**；班级名、学生名、学校名、教辅文案一律换成配置或虚构示例。
2. 永不引入真实数据、第三方教辅内容、维护者本机绝对路径。
3. 测试、冒烟只用临时库与虚构演示数据（`seed_demo_data.py`）。
4. `requirements.txt` 保持最小；新增第三方依赖前确认许可证为 MIT/BSD/Apache-2.0，
   并在提交信息里说明为什么标准库不够用。
5. 每次改动后仓库处于「可运行、测试全绿」状态，不留半成品模块。

### 评审点（异步）

以下节点完成后做一次独立代码评审（精确到文件/行/改法），意见**异步**给，不阻塞继续开发：

- **R1**：P2.0 完成后（正式 schema + 数据层 + CLI 骨架）；
- **R2**：P2.5 完成后（预警涉及写操作与跟进闭环）；
- **R3**：P2.10 收口前（全量回归 + 文档一致性）。

收到意见后按意见补提交。评审只发本仓库内容（公开精简版代码与虚构数据），不附上游实现或课堂内容。

---

## 1. Phase 2 目标与非目标

### 目标

把上游已验证的通用能力，按「纯逻辑 → 数据层 → 业务动作 → 分析与输出 → 界面」的顺序
逐个搬进本仓库。每个模块落地时都是一条**可演示的端到端流程**（虚构数据），
并且把 [MIGRATION_LEDGER.md](MIGRATION_LEDGER.md)「预吸收清单」里对应的通用改进点**一次做对**。

### 非目标（明确不做）

- 题库（导入/检索/推荐）：属 Phase 3，本计划只预留表结构位置，不实现。
- 节假日与调休：Phase 1 已明确不做，Phase 2 仍不做。
- 真实成绩的多学校适配、权限/多用户：不在开源精简版范围内。
- 追平上游功能：功能长期落后上游是预期状态（见 README）。

---

## 2. P2.0 地基：正式 schema + 数据层 + CLI 骨架（已完成 2026-09-23，待 R1 异步意见）

**为什么先做这一步**：Phase 1 的 schema 是临时的（`phase1-temp`），后续所有模块都建在它上面；
必须先有正式的数据模型、统一的数据层入口和 CLI 骨架，后面 9 个模块才有地方落脚。
本步完成后进入评审点 R1。
### 2.1 正式 schema（替换 phase1-temp）

- `schema/` 下按模块分文件，`init_db.py` 按固定顺序执行：
  `core.sql` → `scores.sql` → `homework.sql` → `errors.sql` → `profile.sql` → `alerts.sql`
  （题库表在 Phase 3 加文件，不在本步）。
- 每个文件幂等（`CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`）。
- `meta.schema_version` 记为 `'phase2'`；各表 `created_at` 默认 `datetime('now')`（UTC，约定的第 2 条）。
- `schema/migrations/` 建目录 + `README.md`，约定：此后任何 schema 变更走迁移文件
  （幂等、可重放，文件名形如 `0001_说明.sql`）；已应用的迁移记在独立的
  `schema_migrations(name, applied_at)` 表里，`meta` 只放 `schema_version`、`dataset_version` 这类单值。
- **phase1-temp 的处理**：`init_db.py` 启动时检查 `meta.schema_version`；
  若为 `phase1-temp`，报错并提示「Phase 1 临时库不自动迁移，请加 `--rebuild` 重建」
  （Phase 1 已声明表名与字段不承诺兼容，断裂式替换是预期行为）；
  `--rebuild` 必须先打印将删除的库路径并要求二次确认（`--yes`）。
- **按班级统计的口径写进 schema 评审清单**：凡涉及班级统计的视图/查询，分子分母必须同时限定班级
  （如作业完成率：`student.class = homework.class`），并在注释里写明口径。

建议的表结构（R1 评审时可调，定稿后即为正式模型）：

```
core.sql:    meta(key, value)
             schema_migrations(name PRIMARY KEY, applied_at)   # 迁移记录，独立成表
             classes(id, name UNIQUE, created_at)
             students(id, student_uid UNIQUE, name, class_id 外键→classes, seat_no, created_at)
scores.sql:  exams(id, exam_key UNIQUE, name, exam_date, full_score, created_at)
             exam_scores(id, exam_id 外键→exams, student_id 外键→students, score,
                         UNIQUE(exam_id, student_id), created_at)
             exam_items(id, exam_id 外键→exams, item_no, full_score,
                        UNIQUE(exam_id, item_no))                 # P2.7 小题分析用
             item_scores(id, item_id 外键→exam_items, student_id 外键→students, score,
                         UNIQUE(item_id, student_id))
             # P2.1 的 CSV/Excel 导入先只写 exams/exam_scores；item 表在 P2.7 启用

homework.sql: homework_assignments(id, assign_key UNIQUE, class_id 外键→classes,
                        topic, assigned_date, due_date, created_at)
             homework_submissions(id, assignment_id 外键→homework_assignments,
                        student_id 外键→students, status, submitted_at,
                        UNIQUE(assignment_id, student_id), created_at)
             corrections(id, submission_id 外键→homework_submissions,
                        corrected_at, note, created_at)           # 订正记录
errors.sql:  error_tags(id, code UNIQUE, label)                  # 错因字典
             error_records(id, student_id 外键→students, exam_id 外键→exams 可空,
                        assignment_id 外键→homework_assignments 可空, tag_id 外键→error_tags,
                        note, recorded_at, created_at)
             behavior_records(id, student_id 外键→students, kind, detail,
                        recorded_at, created_at)                  # 行为记录
profile.sql: ability_scores(id, student_id 外键→students, dimension,
                        score, computed_at, UNIQUE(student_id, dimension))
             # 画像=计算结果物化；重算幂等（P2.4）
alerts.sql:   alerts(id, student_id 外键→students, kind, severity,
                      status, created_at, resolved_at)
             follow_ups(id, alert_id 外键→alerts, note, outcome, recorded_by,
                        created_at)                              # 跟进闭环：谁、何时、怎么跟进、结果
```
### 2.2 数据层公共模块 `db.py`（新增）

```python
def connect(db_path):
    # 返回 sqlite3.Connection；row_factory=Row；显式 PRAGMA foreign_keys = ON（约定第 3 条）
def require_schema(conn, expected="phase2"):
    # 版本不对就抛 ConfigError，错误信息写清当前版本与期望版本
def table_exists(conn, name):
    # 从 init_db.py 搬过来
```

- `init_db.connect` 保留为兼容别名（`tests/test_phase1_loop.py` 在用），内部转调 `db.connect`。
- 后续所有模块统一用 `db.connect`，**禁止**各模块自建连接参数。
- **所有数据层函数接受显式连接或 `db_path`，模块内不写死数据库位置**——测试与回归才能安全地指向临时库
  （这条会同时补进 `docs/ENGINEERING_NOTES.md` 第 11 条）。
- 守护测试：除 `db.py` 外，全仓库不得出现 `sqlite3.connect(`，防止绕过外键 PRAGMA。

### 2.3 CLI 骨架 `hub.py`（新增）

- `argparse` 子命令分发，薄分发层，逻辑留在各模块的可导入函数里：
  `init-db / import-scores / make-report`（承接 Phase 1）为第一批子命令。
- 全局 `--config` 与 `--db`（`--db` 覆盖 `paths.database`，放子命令之前；
  不传时走配置，行为向后兼容）。
- 旧三脚本（`init_db.py` / `import_scores.py` / `make_report.py`）保留为兼容垫片：
  转调 `hub.py` 对应函数，保持 Phase 1 的测试与文档命令可用。
- 约定：此后每个模块新增 CLI 能力时，**只加子命令，不加新脚本**。
- 演示数据导入必须是显式的：`import-scores` 不带 `--csv/--excel` 时**报错**而不是悄悄导演示数据，
  演示路径固定为 `import-scores --demo`；旧脚本 `import_scores.py` 作为垫片固定传 `--demo`。

### 2.4 演示数据版本规则

- `seed_demo_data.DATASET_VERSION` 递增到 `demo.v3`（结构变化即递增，规则写死）。
- `validate_dataset_for_import` 保持：版本/内容与配置对不上就拒绝。
- P2.2 起把已生成的 `homework`/`schedule` 数据入库；P2.3/P2.5 起补充错因与预警演示数据。

### P2.0 DoD

- [ ] `schema/` 六个文件 + `migrations/README.md` 落地；`init_db.py --demo --rebuild` 可重建 phase2 库
- [ ] `db.py` 落地；全仓库无第二处自建 sqlite 连接参数
- [ ] `hub.py` 三个子命令与旧三脚本输出一致（旧测试不改全绿）
- [ ] phase1-temp 库不加 `--rebuild` 时被拒绝，且有清晰报错
- [ ] `MIGRATION_LEDGER.md`：新增「正式 schema」行；预吸收清单中
      「外键/schema 完整性/UTC 入库/is None 判空/回归只用临时库」标为已吸收（如已是）
- [ ] R1 评审通过

---

## 3. 模块总览与依赖

```
P2.0 地基（schema/db.py/hub.py）
 ├─ P2.1 成绩导入（正式：CSV 先行 → Excel）
 ├─ P2.2 作业与订正 ─┐
 ├─ P2.3 错因与行为记录 ─┤
 │                      ├─ P2.4 学生画像与能力计算
 ├─ P2.5 预警与跟进闭环 ─┘        │
 └─ P2.7 考试链路 ───────────────┴─ P2.6 周报与阶段巡检
                                       ├─ P2.8 本地 API
                                       └─ P2.9 本地看板前端
P2.10 收口
```
顺序说明：P2.1/P2.2/P2.3/P2.5 可并行开工（都只依赖 P2.0）；P2.4 依赖 P2.1+P2.2+P2.3 的数据；
P2.6 依赖 P2.4+P2.7；P2.8/P2.9 在 P2.6 之后。

---

## 4. P2.1 成绩导入器（正式）

**目标**：从「只吃虚构演示数据」到「能吃使用者真实成绩表」，同时把预吸收清单第 1、4、5、6 条一次做对。

### 上游参照

读上游的真实成绩导入实现（多格式、真实成绩表），注意上游的导入口径约定：
错题导入与成绩导入按（学生、考试名、日期）匹配考试记录，不在同一天运行时两边用同一个
`--exam-date`，否则会产生两行考试记录——**这个口径要原样搬运并写进文档**。

### 分两步走

**P2.1a 导入框架 + CSV（标准库，零新依赖）｜已完成 2026-09-23**

- 新增 `importer.py`：通用导入框架
  - `resolve_student(conn, student_uid=None, name=None)`：
    有 ID 精确匹配；无 ID 用姓名匹配——0 条报错、大于 1 条报错并要求用 ID 指定，
    **禁止「取第一条」**（预吸收 #1，约定第 1 条）。
  - `resolve_exam(conn, name, exam_date)`：按（考试名、日期）匹配，不存在则创建。
  - `dry_run` 预览与真正执行**走同一套查询条件**（预吸收 #4，约定第 4 条）。
  - 整批写入**同一事务**，异常整体回滚（约定第 4 条）。
  - 分数判空用 `is None`，0 分合法（约定第 6 条）。
  - 用户输入进 `LIKE` 时转义百分号与下划线（预吸收 #5，约定第 5 条）。
- 命令：`hub.py import-scores --csv CSV文件 --exam 考试名 --exam-date 日期 [--full-score 100] [--columns 映射] [--dry-run]`
- CSV 列约定写进 `data/README.md`：`student_uid, name, score`（表头可配置 `--columns` 映射）。
- 新模块（`importer.py` 等）落地时同步加进 `tests/test_labels.py` 的 `SCANNED_MODULES`，
  否则 P1.4 的「代码里不出现学科/学段硬编码」会悄悄失守。

**P2.1b Excel 支持（引入 openpyxl）｜已完成 2026-09-23**
- `requirements.txt` 追加 `openpyxl`（MIT），提交信息说明标准库无可用 xlsx 解析。
- 命令：`hub.py import-scores --excel EXCEL文件 [--sheet 表名]`，其余参数与 CSV 一致。
- **CI 目前只安装 `requirements-dev.txt`**：本步要同时让 CI 装上 `requirements.txt`
  （在 `requirements-dev.txt` 里写 `-r requirements.txt`，或给工作流加一步），否则 CI 上会直接红。
- 依赖引入后确认许可证并记进 Phase 4 的依赖许可清单。

### 测试清单（`tests/test_scores_import.py`）

- 同名两条学生 → 姓名导入报错；用 ID 导入成功
- 不存在的 student_uid → 拒绝写入并回滚（库中无残留行）
- `--dry-run` 输出的待写入条数等于实际执行条数
- 0 分成绩正常入库；空分数拒绝
- 重复执行幂等（行数不变）
- `--exam-date` 不同 → 产生两条考试记录；相同 → 同一条
- `--excel` 不传 `--sheet` 时读第一个 sheet（P2.1b）

### P2.1 DoD

- [ ] P2.1a、P2.1b 各自独立提交，提交间测试全绿
- [ ] `data/README.md` 有 CSV/Excel 列约定与示例
- [ ] `MIGRATION_LEDGER.md`：「成绩导入」→ 已搬运；预吸收 #1、#4、#5、#6 标已吸收并写日期
- [ ] Phase 1 的演示导入路径（`--demo`）不受影响，`test_phase1_loop.py` 全绿

---

## 5. P2.2 作业与订正

**目标**：作业布置 → 提交状态 → 订正记录的完整链路；作业完成率统计口径一次写对。

### 上游参照

读上游作业模块的实现与作业完成视图，注意这类统计最容易踩的坑：
完成率的**分子必须限定班级**（转班学生的历史提交会计入错误班级导致完成率超 100%），
修正后的口径是 `student.class = homework.class`。

### Schema

`homework.sql`（P2.0 已建表，本模块启用）：
`homework_assignments` / `homework_submissions` / `corrections`。

### 接口

- 命令：`hub.py import-homework --csv CSV文件 --assign-key 作业标识 --class 班名 --topic 主题 [--dry-run]`
  （学生匹配复用 `importer.resolve_student`，不另起一套）
- 命令：`hub.py homework-stats [--class 班名]`：输出完成率（已交/应交）、迟交、缺交、订正率。
- 完成率口径写进命令 `--help` 与文档：分子分母同时限定班级；
  `avg_score` 类指标注明是「所有考过的人的平均分」还是「本班当前学生」口径
  （上游遗留观察点，本模块定死一种并写明）。

### 演示数据

`seed_demo_data.py` 已生成 `homework` 数据（P1.5）；本模块：

- `DATASET_VERSION` → `demo.v4`；
- `init_db.py --demo` 把 homework 数据入库（assignments + submissions，含 submitted/late/missing）；
- 订正记录演示数据一并生成（submitted 中约 45% 生成 corrections）。

### 测试清单（`tests/test_homework.py`）

- 转班学生场景：A 班历史提交不计入 B 班完成率
- 缺交/迟交/已交计数正确；订正率 = 有订正记录的提交 / 应订正的提交
- `import-homework --dry-run` 与执行条数一致
- 重复导入幂等

### P2.2 DoD

- [ ] 口径文档与 `--help` 一致；转班学生的回归测试存在且通过
- [ ] 台账「作业与订正」→ 已搬运，写清与上游的差异（如：上游的视图名、口径选择）

---

## 6. P2.3 错因与行为记录

**目标**：错因标签体系 + 错因记录 + 学生行为记录的写入与查询。

### 上游参照

读上游错因记录模块，注意写入类操作的通用要求：错因写入要**先验存在**（学生/考试/作业必须存在）
且**单事务**，不留「旧的改了、新的没写」的中间状态。

### Schema

`errors.sql`（P2.0 已建表）：`error_tags` / `error_records` / `behavior_records`。

### 通用化要点
- `error_tags` 字典：内置通用 5 类（`seed_demo_data.ERROR_TAGS` 已有：模型选择、图像读取、计算失误、
  表达不规范、概念混淆），允许使用者在 `config.toml` 的 `[error_tags]` 段扩展；
  内置类不出错，上游的学科强相关标签不搬。
- `error_records` 关联：`exam_id` / `assignment_id` 至少其一非空（CHECK 约束），
  关联行必须存在（外键 + 写入前显式校验，报错信息点名缺的是哪个）。

### 接口

- 命令：`hub.py record-error --student 学生UID [--exam-key 考试标识 | --assign-key 作业标识] --tag 标签代码 [--note 文本]`
- 命令：`hub.py list-errors --student 学生UID [--tag 标签代码]`：按时间倒序。
- 写入走「预览 → 确认 → 单事务写入」（约定第 9 条），`--yes` 跳过确认（脚本用）。

### 测试清单（`tests/test_errors.py`）
- 关联不存在的考试/作业 → 拒绝写入并回滚
- exam_id 与 assignment_id 双空 → CHECK 拒绝
- 自定义 `[error_tags]` 扩展生效；未知 tag code 报错
- list-errors 排序与过滤正确

### P2.3 DoD

- [ ] 台账「错因与行为记录」→ 已搬运；预吸收 #4（单事务/dry-run 同条件）在本模块标已吸收

---

## 7. P2.4 学生画像与能力计算

**目标**：基于成绩、作业、错因的纯计算模块，产出学生能力画像（可重算、幂等）。

### 上游参照

读上游学生画像与能力计算的实现，抽出**纯函数**部分（输入数据 → 输出画像），
IO（读库/写库）留在本模块的命令层。

### Schema

`profile.sql`（P2.0 已建表）：`ability_scores(student_id, dimension, score, computed_at)`。
维度名用英文 key（如 `mechanics`, `experiment`, `computation`），显示名走 labels（可扩展）。

### 接口
- 命令：`hub.py compute-profile [--student 学生UID] [--rebuild]`：幂等重算；
  无 `--rebuild` 时只算新增/变更数据（以 `computed_at` 与源表 `created_at` 比较）。
- 计算函数放 `profiling.py`，纯函数、可单测（不碰数据库）。

### 通用化要点

- 维度与权重进 `config.toml` 的 `[profile]` 段，默认值够跑演示；
- 上游与特定考试挂钩的调参（如某次联考的难度系数）不搬，改为配置项。

### 测试清单（`tests/test_profile.py`）

- 纯函数单测：给定成绩/错因输入，画像输出符合预期
- 幂等：跑两次 `compute-profile`，`ability_scores` 行数与值不变
- 增量：新增一条成绩后重算，只更新受影响的学生

### P2.4 DoD

- [ ] `profiling.py` 无数据库依赖（测试可断言：import 时不建连接）
- [ ] 台账「学生画像与能力计算」→ 已搬运

---

## 8. P2.5 预警与跟进闭环
**目标**：预警扫描 → 预警列表 → 跟进记录 → 解决闭环。本模块完成后进入评审点 R2。

### 上游参照

读上游 `alert_manager` 的实现（`get_conn(db_path)`、`scan_all_students`、`list_alerts`、
`resolve_alert`、`stats`），注意这条通用要求：
所有数据函数接受 `db_path` 参数（见 `docs/ENGINEERING_NOTES.md` 第 11 条），顶层 `--db` 放子命令之前，不传时走默认路径。

### Schema

`alerts.sql`（P2.0 已建表）：`alerts` / `follow_ups`。

### 通用化要点

- 预警规则与阈值进 `config.toml` 的 `[alerts]` 段（如连续缺交 N 次、平均分下滑 X 分），
  默认值够跑演示；上游写死的阈值不搬。
- `resolve_alert` 必须同时写 `follow_ups` 记录（谁、何时、怎么跟进的），不允许「静默解决」。

### 接口

- `hub.py scan-alerts [--db 数据库路径]`：扫描并写入新预警
  （幂等：同一学生在同一规则上未解决的预警不重复建）
- `hub.py list-alerts [--status open|resolved] [--db 数据库路径]`
- `hub.py resolve-alert 预警ID --note 跟进说明 [--db 数据库路径]`
- `hub.py alert-stats [--db 数据库路径]`
- 全部函数签名形如 `(db_path=None, ...)`，`--db` 透传。

### 测试清单（`tests/test_alerts.py`）

- `--db` 指向临时库时，读写都落在该库，默认库无变化
- 重复 `scan-alerts` 不产生重复未解决预警
- `resolve-alert` 后 `follow_ups` 有记录，状态变为 resolved
- 阈值配置变更后扫描结果随之变化（证明阈值不是硬编码）

### P2.5 DoD

- [ ] `--db` 指向临时库的回归测试存在且通过
- [ ] 台账「预警与跟进闭环」→ 已搬运；预吸收 #1（ID 关联）如有新增落点一并标已吸收
- [ ] R2 评审通过

---

## 9. P2.6 周报与阶段巡检

**目标**：周报（上周数据汇总）与阶段巡检（当前阶段进度/覆盖/异常）的 Markdown 输出。

### 上游参照

读上游周报与阶段巡检的实现，抽出「汇总逻辑」；上游面向特定班级的文案改成 labels/config。

### 接口

- 命令：`hub.py weekly-report [--week 周次，格式YYYY-Www | 默认上周] [--class 班名]`
  输出到 `outputs/weekly_周次.md`
- 命令：`hub.py phase-patrol` → `outputs/phase_patrol_日期.md`：
  当前阶段、教学周进度、作业覆盖、预警未解决数、异常提醒。
- 报告只写相对路径（约定：可分享文件不带本机绝对路径，见 `make_report.display_path`）。

### 测试清单（`tests/test_reports.py`）

- 给定虚构数据，周报数字与直接查库一致
- `--week` 指定未来周 → 报错（不在学期内）
- 报告中不出现本机绝对路径（复用 `test_phase1_loop` 的断言思路）

### P2.6 DoD

- [ ] 台账「周报与阶段巡检」→ 已搬运

---

## 10. P2.7 考试链路（分析 / 讲评 / 讲义）

**目标**：小题级考试分析（得分率、难度、区分度）+ 讲评提纲/讲义 Markdown 生成。

### 上游参照

读上游考试链路的实现（分析、讲评、讲义），注意版权边界：**示例题必须自制**
（[PRIVACY.md](PRIVACY.md) 第 1 节），真实试卷题一律不搬。

### Schema

`scores.sql` 中的 `exam_items` / `item_scores`（P2.0 已建表，本模块启用）。

### 接口

- 命令：`hub.py import-item-scores --csv CSV文件 --exam-key 考试标识`：小题得分导入
  （列约定：`student_uid, item_no, score`；学生匹配复用 `importer.resolve_student`）
- 命令：`hub.py exam-analysis --exam-key 考试标识` → `outputs/exam_analysis_考试标识.md`：
  每题得分率、难度分档、区分度（高低分组法，方法写进文档）。
- 命令：`hub.py make-handout --exam-key 考试标识`：讲评讲义 Markdown（自制示例题占位 + 失分点分析）。

### 测试清单（`tests/test_exam_chain.py`）

- 得分率/难度计算与手工验算一致（含 0 分小题）
- 讲义中不出现任何真实题干（自制示例题有固定标记，可断言）
- 重复导入幂等

### P2.7 DoD

- [ ] 台账「考试链路」→ 已搬运，差异写清「示例题自制」

---

## 11. P2.8 本地 API

**目标**：只读本地 API，供看板前端与外部脚本查询。

### 约定（预吸收 #8，约定第 8 条，一次做对）

- 只绑定 `127.0.0.1`，端口默认 `8420`（`--port` 可改）；
- CORS 只回固定的本机 Origin（含实际端口）并带 `Vary: Origin`，**禁止**通配符星号；
- 第一版只做 GET（只读），写操作留待后续版本；
- 标准库 `http.server` 实现，不引入 web 框架。

### 接口

- 命令：`hub.py serve [--port 8420]`
- `GET /api/classes`、`GET /api/exams`、`GET /api/exam/{exam_key}/stats`、
  `GET /api/class/{班名}/averages`（JSON，UTF-8）。
- 错误返回 JSON 错误体 + 正确 HTTP 状态码（404/400 区分）。

### 测试清单（`tests/test_api.py`）

- 用临时库起服务（随机空闲端口），断言各端点 200 与字段
- 不存在的 exam_key → 404 + JSON 错误体
- 响应头无通配符 CORS（`Access-Control-Allow-Origin: 星号` 不出现）
- 服务只监听 127.0.0.1（断言 socket 绑定地址）

### P2.8 DoD

- [ ] 台账「本地 API」→ 已搬运；预吸收 #8 标已吸收

---

## 12. P2.9 本地看板前端

**目标**：本地看板页面（静态站），数据来自 P2.8 API 或构建时嵌入。

### 上游参照
读上游看板前端的实现，注意展示层的通用要求：
空值统一显示「—」、排序与统计要排除 null、数字格式化要有兜底（不要直接崩在 null 上）。

### 方案（推荐构建时嵌入，简单可靠）

- 命令：`hub.py make-dashboard`：从本地库生成 `outputs/dashboard/` 静态站
  （`index.html` + `data.json`，数据构建时嵌入，不依赖运行时 API）；
- 页面：班级选择、考试平均分趋势、预警未解决列表、作业完成率；
- 全部文案走构建时注入：模板只留占位符，文字从 `labels/<locale>.toml` 取，
  和 P1.4 的收口保持一致（不要因为「模板不是 .py」就把中文写死进去）；
- 无第三方 CDN 依赖（离线可用，隐私边界要求）。

### 测试清单（`tests/test_dashboard.py`）

- 生成的 `data.json` 可解析，数值与直接查库一致
- 空成绩/空预警时页面数据为 null，前端显示「—」（断言模板含 fmtScore 类处理）
- `outputs/dashboard/` 下无本机绝对路径

### P2.9 DoD

- [ ] 双击 `outputs/dashboard/index.html` 可离线查看（含虚构数据演示）
- [ ] 台账「本地看板前端」→ 已搬运

---

## 13. P2.10 收口

- [ ] 全模块台账状态为「已搬运」，差异列完整
- [ ] README「现在能做什么」更新为 Phase 2 能力；ROADMAP 顶部状态更新为 Phase 2 完成
- [ ] 四条绿灯命令全绿；`bash scripts/privacy_scan.sh --strict` 人工审计一次
      （本机产物列出属正常，确认无真实数据混入）
- [ ] `docs/PHASE2_PLAN.md` 本文件顶部状态改为「已完成」
- [ ] R3 评审通过：全量回归 + 文档一致性（README/ROADMAP/台账/各模块 `--help` 互相印证）
- [ ] 发布前硬性待办（ROADMAP）：依赖许可清单、`git log` 全历史隐私扫描——列出清单，
      执行留到 Phase 4 发布前

---

## 14. 风险与待定事项
| # | 事项 | 处理 |
| --- | --- | --- |
| 1 | 上游 MiniMax 已废弃 | hub 的任何外部模型能力必须做成**可配置后端**（约定第 7 条：校验业务错误码、显式超时），不硬编码任何厂商；Phase 2 如无对应模块则不做 |
| 2 | Excel 解析依赖 | 维护者已决定保留 Excel 支持：P2.1b 引入 `openpyxl`（MIT），并同步让 CI 装上运行时依赖 |
| 3 | PDF 成绩导入 | 上游有 PDF 导入路径；Phase 2 待定（P2.1b 后评估），不承诺 |
| 4 | `avg_score` 口径 | P2.2 定死一种口径并文档化（见 §5） |
| 5 | 题库 | Phase 3，不在本计划；`schema/` 预留文件名即可 |
| 6 | 多用户/权限 | 不在开源精简版范围，任何模块不做 |

---

## 附录 A：预吸收清单 → 模块映射

| 预吸收清单项 | 落点模块 | 状态 |
| --- | --- | --- |
| 身份一律按 ID 关联，姓名匹配歧义要报错 | P2.1a（`importer.resolve_student`）、P2.5 | 待吸收 → P2.1a 落地 |
| 入库时间统一 UTC，展示再转时区 | P2.0（schema 默认值） | 已吸收 |
| 连接开启外键、schema 完整性校验、缓存按库路径记忆 | P2.0（`db.py`） | 已吸收 |
| 一组写入放同一事务，dry-run 与执行同条件 | P2.1a、P2.3 | 待吸收 → 落地 |
| SQL 参数化；用户输入不做 LIKE 通配符 | P2.1a 起各模块 | 待吸收 → P2.1a 落地 |
| 判空用 `is None`，0 是合法值 | P2.0 起 | 已吸收 |
| 对外调用校验业务错误码，HTTP 200 不算成功 | P2.8（纯本地 API 不触发）/ 风险 1 | 待定 |
| 本地服务只绑本机，CORS 不用通配符 | P2.8 | 待吸收 → P2.8 落地 |
| 外发默认脱敏，带明细要显式授权 | 暂无外发模块；P2.6/P2.9 输出物保持「虚构数据/相对路径」 | 持续 |
| 回归检查只用临时库与演示数据 | 全程 | 已吸收 |

## 附录 B：最终 CLI 一览（Phase 2 完成时）

```
python3 hub.py --help
python3 hub.py [--config 配置文件] [--db 数据库路径] init-db --demo [--rebuild --yes]
python3 hub.py import-scores (--csv CSV文件 | --excel EXCEL文件) --exam 考试名 --exam-date 日期 [--sheet 表名] [--dry-run]
python3 hub.py make-report
python3 hub.py import-homework --csv CSV文件 --assign-key 作业标识 --class 班名 --topic 主题 [--dry-run]
python3 hub.py homework-stats [--class 班名]
python3 hub.py record-error --student 学生UID [--exam-key 考试标识 | --assign-key 作业标识] --tag 标签代码 [--note 文本] [--yes]
python3 hub.py list-errors --student 学生UID [--tag 标签代码]
python3 hub.py compute-profile [--student 学生UID] [--rebuild]
python3 hub.py scan-alerts / list-alerts [--status open|resolved] / resolve-alert 预警ID --note 跟进说明 / alert-stats
python3 hub.py weekly-report [--week 周次YYYY-Www] [--class 班名]
python3 hub.py phase-patrol
python3 hub.py import-item-scores --csv CSV文件 --exam-key 考试标识
python3 hub.py exam-analysis --exam-key 考试标识
python3 hub.py make-handout --exam-key 考试标识
python3 hub.py serve [--port 8420]
python3 hub.py make-dashboard
```

旧三脚本（`init_db.py` / `import_scores.py` / `make_report.py`）保留为兼容垫片。

---

## 修订记录

### R1 评审结论（2026-09-23，P2.0 范围）

结论：结构可用，继续推进；发现 3 条必修、4 条建议、若干 nits，**全部已修**（同一次提交内附回归测试）。

| 编号 | 问题 | 处理 |
| --- | --- | --- |
| R1-1 | 已是 phase2 的库上 `--rebuild --yes` 静默无效（实测标记行仍在） | 改为无条件重建（仍要求 `--yes`），并打印被删除的文件 |
| R1-2 | 文件不是 SQLite 库时抛原始 traceback | `db.py` 把 `sqlite3.DatabaseError` 翻成可操作 `ConfigError`；`hub.py` 顶层兜底 `sqlite3.Error`，统一退出码 2 |
| R1-3 | 二次导入改 `--full-score` 静默不生效（实测库里仍是旧值） | 检测到不一致时更新满分并输出告警；dry-run 只告警不写库 |
| R1-4 | schema 缺 CHECK 约束（可能写入负分、满分为 0） | `scores.sql` 补 `score >= 0`、`full_score > 0` |
| R1-5 | `homework_assignments.class_id`、`error_records.assignment_id` 缺索引 | 两个索引补齐，并有「索引存在」的断言 |
| R1-6 | 连接守护测试只扫顶层 `*.py` | 改为递归扫描并排除 `tests/`、虚拟环境目录 |
| R1-7 | `db.py` 的提示指向 `init_db.py`，与 hub 其它提示不一致 | 统一指向 `hub.py` |
| R1-8~11 | 文档头表述过时、迁移非原子性未注释、`recorded_at`/`created_at` 语义未写明、测试内联配置重复 | 一并修掉 |

新增约定：`docs/ENGINEERING_NOTES.md` 第 12 条（显式开关要真的生效；底层错误翻译成人话）。
注：schema 文件改动只影响**新建**库，已有本地 phase2 库请用 `init-db --demo --rebuild --yes` 重建（Phase 2 未发布，不补迁移文件）。

2026-09-23（入库前修订）：

1. `alerts.student_id` 的外键由 `→alerts` 改正为 `→students`（原稿笔误）。
2. 迁移记录从 `meta` 拆成独立的 `schema_migrations(name, applied_at)` 表；`meta` 只放单值。
3. `follow_ups` 补 `outcome` / `recorded_by`，与 P2.5「谁、何时、怎么跟进」的要求对齐。
4. 「数据层函数接受显式连接或 `db_path`」升为全局约定，并补进 `docs/ENGINEERING_NOTES.md` 第 11 条；新增「除 `db.py` 外不得出现 `sqlite3.connect(`」守护测试。
5. `import-scores` 不带 `--csv/--excel` 时必须报错，演示导入固定走 `--demo`（旧脚本垫片传 `--demo`），避免把演示数据当真实成绩导进去。
6. 补上 CI 依赖缺口：`requirements-dev.txt` 要能装上 `requirements.txt`，否则 P2.1b 的 `openpyxl` 在 CI 上装不上。
7. 新模块要同步加进 `tests/test_labels.py` 的 `SCANNED_MODULES`；P2.9 的前端文案改为构建时从 labels 注入，不写死在模板里。
8. 原稿里的上游内部编号（`C8`、`风险 3`、`P1 教训`）改写成通用教训本身——仓库文件不引用上游内部文档（`AGENTS.md` 硬规则第 6 条）。
9. 评审点改为异步：意见不阻塞开发，收到后补提交；评审只发本仓库内容。
