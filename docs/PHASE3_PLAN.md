# Phase 3 详细计划：题库与教材隔离

> 状态：**已定稿（2026-09-26）**——维护者已确认三处决策：升级路径走「真迁移」（决策一 A）、
> 自制示例题放顶层 `examples/`（决策二 A）、示例题由执行者起草、维护者审物理正确性（决策三 A）；
> 评审保持单点 R4。Phase 2 已于 2026-09-25 收口（P2.0～P2.10 全部落地，R1/R2/R3 已闭环）；
> P3.0～P3.4 已于 2026-09-26 落地（题库 schema + 数据层 + 非破坏式升级链路；
> 导入器 + 自制示例题集，演示数据集升到 `demo.v7`；检索与推荐的 CLI 与只读 API；
> 组卷讲义与讲评讲义分文件、默认不含答案）。
> 示例题已于 2026-09-26 经维护者人工核对
> 物理正确性通过（决策三 A 闭环），见修订记录第 11 条。
> 范围锚点：[ROADMAP.md](ROADMAP.md) 的 Phase 3。本文件只细化步骤，不扩大范围；
> Phase 4（文档与发布）不在本计划内。
>
> 上游指作者的私有教学系统。本文件及本仓库任何文件**不记录上游的本地路径**
> （[AGENTS.md](../AGENTS.md) 硬规则第 6 条）；执行时先读上游对应模块的实现，再用通用写法重写。

---

## 0. 给执行者（Codex）的一页纸

### 开工前必读（按顺序）

1. `docs/ROADMAP.md` —— 确认 Phase 3 在路线中的位置
2. `docs/MIGRATION_LEDGER.md` —— 台账现状（题库行目前为「未开始」）
3. `docs/PRIVACY.md` —— 隐私与版权边界（本阶段重点）
4. `docs/ENGINEERING_NOTES.md` —— 14 条工程约定，写代码时默认遵守
5. 本文件 —— 按模块顺序执行

### 分支与提交

- 一个模块一次提交（P3.0～P3.5 各一次），提交信息格式：`P3.x 做了什么；与上游的差异：一句话`；
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

1. 只读上游实现，**用通用写法重写**；班级名、学生名、学校名、题目内容一律换成配置或自制示例。
2. 永不引入真实数据、第三方题目/教材内容、维护者本机绝对路径。
3. **题目是用户数据**：用户的题库只落在 `data/`（`.gitignore` 拒绝，语义不变）；
   仓库里唯一的题目是自制示例题，放顶层 `examples/`（≤15 道，见附录 B）。
4. 测试、冒烟只用临时库与虚构演示数据（`seed_demo_data.py`）。
5. `requirements.txt` 保持最小；新增第三方依赖前确认许可证为 MIT/BSD/Apache-2.0，
   并在提交信息里说明为什么标准库不够用。
6. 每次改动后仓库处于「可运行、测试全绿」状态，不留半成品模块。

### 评审点（单点，异步）

- **R4**：P3.4 完成后、P3.5 收口前 —— 全量回归 + 题库隔离专项（§1 的四条隔离逐条核对）+ 文档一致性。

意见**异步**给，不阻塞继续开发；收到后按意见补提交。评审只发本仓库内容
（公开精简版代码与虚构数据），不附上游实现或课堂内容。

---

## 1. Phase 3 目标与非目标

### 为什么是「隔离」而不是「题库功能」

Phase 3 的风险模型与其他阶段不同：题目内容天然携带版权风险（第三方试卷、教辅原文、
教材节选）。本阶段的完成定义不是「题库多好用」，而是「**框架可用、内容隔离**」。
四条隔离是本阶段所有设计的最高约束，评审 R4 逐条核对：

1. **内容隔离**：用户的题库是用户数据，只落在 `data/`（`.gitignore` 拒绝），永不提交；
   仓库里唯一的题目是自制示例题，放顶层 `examples/`，不放进 `data/`——
   `data/` 的语义（「除 `data/README.md` 外一律禁止入仓」，见 `docs/PRIVACY.md` 第 4 节）
   不为演示文件开口子。
2. **版权隔离**：自制示例题必须原创（附录 B），不得改编任何试卷/教辅/网络题目；
   原创声明写在 `examples/README.md`（JSON 不支持注释，题集文件保持纯数组）；
   导入器文档明确告知用户只能导入自己拥有权利的内容。
3. **结构隔离**：`schema/questions.sql` 独立文件；`meta.schema_version` 升为 `phase3`，
   存量 `phase2` 库走 `schema/migrations/0001_questions.sql` + 新的 `hub.py upgrade-db`
   做**非破坏式升级**（决策一 A，见 §2）；题目表与成绩/作业/画像表只通过 `question_key`
   与标签做松耦合关联，不建跨域外键。
4. **接口隔离**：检索与推荐是纯只读接口；推荐算法与存储解耦，只读 `errors.py` / `profiling.py`
   的输出，不写库。

「教材隔离」的含义：本阶段「教材」指讲义与教学材料产出物（`make-handout`）。隔离手段是
模板白板化——结构走 `labels`，内容只放自制示例或用户题库；模板与示例里不出现任何
第三方教材原文。落点在 P3.4。

### 目标

- 题目结构落库：题型 / 题干 / 选项 / 答案 / 解析 / 难度 / 知识点标签（P3.0）；
- 升级链路打通：`phase2 → phase3` 走迁移，不删库、不丢数据，并把迁移机制本身验一遍（P3.0）；
- 导入器：JSON 为主、CSV 为辅，dry-run 与执行同条件、整批单事务、`question_key` 全局唯一（P3.1）；
- 检索接口：CLI + 本地 API（只读），答案默认隐藏（P3.2）；
- 推荐接口：按错因标签与画像弱项推荐，纯只读，返回可解释的推荐理由（P3.3）；
- 组卷/讲义集成：`make-handout` 可从题库按 key 组卷或按推荐组卷（P3.4）；
- 演示数据带自制示例题集，`DATASET_VERSION` 从 `demo.v6` 升到 `demo.v7`（P3.1）。

### 非目标（明确不做）

- 在线题库同步、外部 API 调用：不做（延续 Phase 2「对外调用不适用」的结论）。
- PDF / Word 题目导入：待定，不承诺（见待定清单）。
- 图片题、公式渲染：不做，题面纯文本（Markdown 子集）。
- 协同过滤 / IRT 等复杂推荐算法：不做，规则推荐够演示。
- 题目使用统计（哪道题被组过几次）：不做；推荐不写库。
- 多用户/权限：延续不做。
- 追平上游题库功能：功能长期落后上游是预期状态（见 README）。

---

## 2. P3.0 题库 schema + 数据层 + 升级链路（已完成 2026-09-26）

**为什么先做这一步**：题目是后面四个模块的数据地基；且这是 Phase 2 收口后的第一次
schema 变更，必须把 `schema/migrations/` 的迁移链路**真正打通**（决策一 A）——
现在没有真实数据、重建成本为零，是验证升级路径最便宜的时候；发布之后每次升级都要靠它。

### 2.1 升级策略（先定死，再写表）

现状（Phase 2 代码）是「版本不等就必须 `--rebuild --yes`」：迁移文件永远不会在存量库上执行。
本次把它改成**已知旧版本走迁移、未知版本才要求重建**：

- `db.SCHEMA_VERSION` → `phase3`；新增 `db.MIGRATABLE_SCHEMA_VERSIONS = ("phase2",)`；
- `db.require_schema` 三分支：当前版本放行；`phase1-temp` 与未知版本仍要求 `--rebuild --yes`；
  **已知旧版本**报「库落后，请先跑 `python3 hub.py upgrade-db`（不会删除数据）」；
- 新增 `hub.py upgrade-db`：只应用迁移 + 校验表齐全 + 更新 `meta.schema_version`，
  **不导入任何演示数据、不写业务表**（`init-db` 必须带 `--demo`，不能拿来升级真实库）；
- `init_db.init_database` 复用同一段升级逻辑：存量 `phase2` 库不再被拒，走迁移后照常建库。

### 2.2 表结构

```
questions.sql: questions(id, question_key TEXT UNIQUE NOT NULL, qtype TEXT NOT NULL,
                          stem TEXT NOT NULL CHECK(length(trim(stem))>0),
                          options_json TEXT, answer TEXT NOT NULL CHECK(length(trim(answer))>0),
                          analysis TEXT,
                          difficulty INTEGER NULL CHECK(difficulty IS NULL
                                                        OR difficulty BETWEEN 1 AND 5),
                          source_label TEXT, created_at 默认 datetime('now') UTC)
               # qtype 取值：choice / fill / calculation / experiment / other（CHECK 约束）
               # choice 必须带 options_json（CHECK 约束）；options_json 是选项数组的 JSON
               # difficulty 允许 NULL，语义是「未标难度」，不是 0（NOTES 第 6、13 条）
               # source_label 只写「自制示例」或用户自填的来源说明
               question_tags(question_id 外键→questions ON DELETE CASCADE,
                             tag TEXT NOT NULL CHECK(length(trim(tag))>0),
                             PRIMARY KEY(question_id, tag))
               # 标签是知识点标签（如「牛顿第二定律」），与错因标签是两套词典，映射走配置
               索引：idx_questions_qtype、idx_question_tags_tag
```

### 2.3 落地方式

- 新库：`init_db.py` 执行顺序追加 `questions.sql`
  （`core → scores → homework → errors → profile → alerts → questions`）。
- 存量 `phase2` 库：`schema/migrations/0001_questions.sql`（幂等，与 `questions.sql` 同 DDL），
  经 `hub.py upgrade-db` 应用后记 `schema_migrations`，再把 `meta.schema_version` 升为 `phase3`；
  表齐全性校验通过之前**不写版本号**。
- 新库路径也会把已随库分发的迁移记进 `schema_migrations`（DDL 幂等，等于 no-op），
  这样「新库」与「迁移后的老库」结构与迁移记录一致。
- `questions.py`：数据层原语，函数接受显式连接（NOTES 第 11 条），事务边界留给调用方
  （导入器在 P3.1 里统一开一个事务，避免嵌套提交）；
  除 `db.py` 外不得出现 `sqlite3.connect(`（守护测试已递归覆盖全仓库）。
- 标签词典：`config_loader.py` 新增 `[question_bank]`（`tags` 白名单 + `tag_map` 映射），
  与 `errors.py` 的 `[error_tags]` 是两套词典，显式配置、不做隐式耦合；
  白名单为空时导入器报「请先配置标签白名单」，不静默放行。

### 2.4 验收

- 新库：七个 schema 文件按序执行，`meta.schema_version = 'phase3'`，
  `questions` / `question_tags` 与两个索引都在。
- 存量库升级：手工构造 `phase2` 库（含既有业务数据）→ `hub.py upgrade-db` 后
  题目表可用、`schema_migrations` 有 `0001_questions.sql`、版本为 `phase3`、**原有数据仍在**；
  重复执行幂等（第二次不重复记录、不报错）。
- 结构一致：新库（建表脚本）与「`phase2` 库 + 迁移」的 `sqlite_master`（表/索引/DDL 文本）一致。
- 落后版本访问：未升级的 `phase2` 库跑 `make-report` 等命令时报可操作错误（提到 `upgrade-db`）、
  退出码 2、无 traceback；`phase1-temp` 与未知版本仍指 `--rebuild --yes`。
- 约束：非法 `qtype`、难度越界、空题干、空答案、`choice` 缺选项都被拒绝；`difficulty` 允许 NULL（「未标难度」）。
- 数据层：插入/读取/标签级联删除正确；重复 `question_key` 报可操作错误。
- 守护测试：`questions.py` 无 `sqlite3.connect`；`tests/test_labels.py` 的 `SCANNED_MODULES`
  已加入 `questions.py`（沿用 PHASE2_PLAN 文末「入库前修订」第 7 条）。

改动文件：`schema/questions.sql`（新增）、`schema/migrations/0001_questions.sql`（新增）、
`schema/migrations/README.md`、`db.py`、`init_db.py`、`hub.py`、`questions.py`（新增）、
`config_loader.py`、`config.example.toml`、`tests/test_questions.py`（新增）、
`tests/test_schema.py`、`tests/test_db.py`、`tests/test_labels.py`、`tests/test_config.py`、
`README.md`（去掉过期的 `phase1-temp` 提醒）、`docs/ROADMAP.md`、`docs/MIGRATION_LEDGER.md`。

---

## 3. P3.1 题目导入器 + 自制示例题集（已完成 2026-09-26，示例题物理正确性已复核通过）

### 导入器

- 格式：JSON 为主（数组，每题一个对象，字段名与表列对应）、CSV 为辅（扁平列）；
  `data/README.md` 声明两种格式的字段说明与示例。
- 沿用 P2.1 的工程约定：`question_key` 全局唯一（批内或库内重复都整批报错）；
  dry-run 与执行共用同一份导入计划；整批单事务，失败整批回滚、库里无残留；
  题干/答案为空拒绝；标签不在 `[question_bank] tags` 白名单时报错并指出是哪道题哪个标签。
- CLI：`hub.py import-questions (--json 文件 | --csv 文件) [--dry-run]`；
  `hub.py import-questions --demo` 灌入自制示例题（供演示与测试的显式入口）。
- `import-questions` 不带 `--json/--csv/--demo` 时必须报错（沿用 PHASE2_PLAN 文末「入库前修订」
  第 5 条的教训：演示导入固定走 `--demo`，避免把演示数据当用户题目导进去）。

### 自制示例题集

- `examples/questions_demo.json`：≤15 道，覆盖 `choice` / `fill` / `calculation` / `experiment`
  四种题型，难度 1–5 尽量拉开，每题 1–3 个知识点标签（必须在白名单内）。
- `examples/README.md`：原创声明、编写日期、格式说明与「只能导入你有权使用的内容」的提醒
  （题集本身保持纯 JSON，不写注释）。
- 示例题由执行者起草，维护者已于 2026-09-26 人工核对物理正确性通过后合入（决策三 A 闭环，见修订记录第 11 条）。
- `seed_demo_data.py`：按配置种子确定性生成示例题数据（复用示例题集内容），`--check`
  校验题目数量、标签合法性与两次生成一致性；`DATASET_VERSION` 从 `demo.v6` 升到 `demo.v7`。

### 验收

- 同一份文件 dry-run 与执行除写库外结果一致；非法行（空题干、重复 key、非法标签）
  整批回滚，`questions` 与 `question_tags` 均无残留。
- `--demo` 导入后题目数等于示例集数量，标签全在白名单。
- 白名单为空时导入被拒绝并提示怎么配。
- `seed_demo_data.py --check` 通过；两次生成逐字节一致。
- `data/README.md` 有题目格式说明；示例题集随默认隐私扫描一起过（它被 Git 跟踪，
  不需要新增放行规则）。

改动文件：`questions.py`（导入器）、`hub.py`、`data/README.md`、`examples/questions_demo.json`（新增）、
`examples/README.md`（新增）、`seed_demo_data.py`、`config.example.toml`、`tests/test_questions.py`。

---

## 4. P3.2 检索接口（已完成 2026-09-26）

- CLI：`hub.py list-questions [--tag 标签] [--type 题型] [--difficulty 1-5] [--keyword 关键词] [--limit N]`；
  输出 `question_key` / 题型 / 难度 / 标签 / 题干摘要（题干只显示前 60 字）；
  **答案默认不显示**，`--show-answer` 才显示（防讲评时误触答案）。
- API：`GET /api/questions?tag=&type=&difficulty=&q=`（只读；沿用 `api.py` 的
  只绑 `127.0.0.1` + CORS 白名单 + 统一 JSON 错误体约定）。
  接口**不返回** `answer` / `analysis` 字段——本地 API 无鉴权，答案只走 CLI 的显式开关。
- 关键词检索用 `LIKE` + 转义：复用 `importer.escape_like`，如需抽成公共函数记一次小重构
  （不复制第二份转义实现）。
- 空题库时输出可操作的「题库为空，先跑 `import-questions`」提示，不抛 traceback。

### 验收

- 各过滤条件可单独/组合使用，结果正确；`--limit` 生效。
- 关键词含 `%` / `_` / 反斜杠时按字面匹配（回归测试，沿用 P2.1 的转义约定）。
- 答案默认隐藏的测试：不带 `--show-answer` 时输出不含答案与解析；API 响应里没有这两个字段。
- API 端点行为与现有只读端点一致（404/400 错误体格式一致）。

改动文件：`questions.py`、`hub.py`、`api.py`、`tests/test_questions.py`、`tests/test_api.py`（按现有结构追加）。

---

## 5. P3.3 推荐接口（已完成 2026-09-26）

- CLI：`hub.py recommend-questions --student UID [--limit N]`。
- 推荐逻辑（规则推荐，纯函数便于测试）：
  1. 读该学生的错因标签分布（`errors.py`）与画像弱项维度（`profiling.py`）；
  2. 经 `[question_bank.tag_map]` 把错因标签映射到知识点标签；
  3. 按「标签命中数 × 难度适配」排序；难度适配按画像综合分分档（写死一份对照表，
     进文档与测试）：综合分 < 60 → 难度 ≤ 2；60–79 → 难度 ≤ 3；≥ 80 → 难度 ≤ 4；
     无画像数据时按难度 ≤ 3；
  4. 返回题目 + 可解释的推荐理由（如「命中错因标签『受力分析』2 次」）。
- **纯只读**：推荐结果不写库（避免推荐记录膨胀；如需留痕以后再议，见待定清单）。
- 无错因/画像数据时退化为按难度分布返回通用题目，并明示「暂无错因数据，返回通用推荐」
  （沿用 R2 修订的 NOTES 第 13 条：没有数据 ≠ 差数据）。
- 学生 UID 不存在时报可操作错误。
- API：`GET /api/questions/recommend?student_uid=`（只读，同样不回答案）。

### 验收

- 有错因数据的学生：推荐结果非空、理由非空且与输入数据一致（理由里提到的标签确实在该学生错因记录里出现过）。
- 无数据学生：走退化路径且输出有明示；不把「没数据」当成「不需要推荐」。
- 推荐前后 `questions` / 相关表行数不变（只读断言）。
- `tag_map` 缺失映射时不崩溃，跳过该标签并在理由里说明。

改动文件：`questions.py`、`hub.py`、`api.py`、`config.example.toml`（`[question_bank.tag_map]` 示例）、
`tests/test_questions.py`。

---

## 6. P3.4 组卷与讲义集成（已完成 2026-09-26）

- `hub.py make-handout` 新增两种模式，与现有 `--exam-key` 模式**互斥**（互斥时报错指引，不静默忽略）：
  - `--question-keys k1,k2,...`：按 `question_key` 点题组卷；
  - `--recommend-for UID [--limit N]`：用 P3.3 的推荐结果组卷。
- `question_key` 不存在时整批报错、不落文件（沿用「一组写入同一事务」的精神：要么整份讲义生成，要么不生成）。
- 讲义模板：「教材隔离」落点——结构走 `labels` 的 `[handout]` 段（新增 key，进 `tests/test_labels.py` 清单）；
  内容只放自制示例或用户题库题目；模板文件不嵌入任何第三方原文。
- 组卷讲义与现有讲评讲义分开命名（现有是 `handout_<exam_key>.md`，
  组卷用 `handout_questions_<键摘要>.md`），避免互相覆盖。
- **答案与解析默认不附**（课堂投影优先）；`--with-answer` 才附在文末。
- 输出 Markdown 到 `outputs/`（相对路径、可分享，沿用 P2.6/P2.9 约定）。

### 验收

- 三种模式互斥校验：同时给两种时报错并指引正确用法。
- `question_key` 不存在时退出码非零且 `outputs/` 无残留文件。
- 默认生成的讲义不含答案；加 `--with-answer` 才附。
- 生成的讲义中自制示例题带原创声明标记；模板扫描无第三方内容（人工核对一次）。
- README 与 `--help` 的 `make-handout` 说明同步更新。

改动文件：`exam.py`（`make-handout`）、`hub.py`、`labels/zh-CN.toml`、`tests/test_exam_chain.py`（按现有结构追加）、
`tests/test_labels.py`、`README.md`。

---

## 7. P3.5 收口

- 四条绿灯命令全绿；CI 绿。
- 文档收口：
  - README「现在能做什么」补题库四条命令（`import-questions` / `list-questions` /
    `recommend-questions` / `make-handout` 新模式）与 `upgrade-db`；
  - ROADMAP 顶部状态 → **Phase 3 已完成**，下一步 Phase 4（文档与发布）；
  - MIGRATION_LEDGER 题库行 → 「已搬运」，差异写清：框架按本仓库数据约定自研
    （`question_key` 唯一、标签白名单、纯只读推荐），不搬上游的整卷解析与图片题；
    内容隔离是本仓库新增的硬约束；
  - `data/README.md` 题目格式说明复核。
- 隔离专项自查（R4 前执行者先自查，评审复核，**四条逐条**）：
  1. 内容隔离：`git ls-files` 里没有用户题目；`examples/` 只有自制示例题与说明；
     往 `data/` 放一份用户题库文件后 `git status` 仍然干净。
  2. 版权隔离：自制示例题逐题确认非改编（维护者已审物理正确性）；原创声明在 `examples/README.md`。
  3. 结构隔离：`questions.sql` 独立文件，`schema_migrations` 记录完整，
     题目表没有指向成绩/作业/画像域的跨域外键。
  4. 接口隔离：检索与推荐是只读的（有行数不变断言），推荐只读 `errors.py` / `profiling.py` 的输出。
- 评审点 R4（异步）：全量回归 + 上面四条隔离专项 + 文档一致性。

---

## 待定清单

| 编号 | 事项 | 结论 |
| --- | --- | --- |
| 1 | PDF / Word 题目导入 | 待定，不承诺（真做时先确认解析库的许可证，沿用 P2.1b 的 `openpyxl` 流程） |
| 2 | 图片题、公式渲染 | 不做；题面纯文本 |
| 3 | 协同过滤 / IRT 等推荐算法 | 不做；规则推荐够演示 |
| 4 | 题目使用统计（推荐记录落库） | 不做；推荐保持只读 |
| 5 | 题库条目的更新与删除 | 首版重复 `question_key` 整批报错、没有删除命令；等真实使用需求出现再定 |
| 6 | 题号 ↔ 题库题目的关联 | 不做：`exam_items` 只有题号，不与 `questions` 建关联，所以推荐只按错因标签与画像，不按「哪道题做错了」 |
| 7 | 多用户/权限 | 延续不做 |
| 8 | 节假日与调休 | 延续不做（Phase 1 起即不做） |

---

## 附录 A：最终 CLI 一览（Phase 3 完成时）

Phase 2 的全部命令保持不变，新增：

```
python3 hub.py upgrade-db                                             # 存量 phase2 库非破坏式升级
python3 hub.py import-questions (--json JSON文件 | --csv CSV文件) [--dry-run]
python3 hub.py import-questions --demo
python3 hub.py list-questions [--tag 标签] [--type 题型] [--difficulty 1-5] [--keyword 关键词] [--limit N] [--show-answer]
python3 hub.py recommend-questions --student 学生UID [--limit N] [--show-answer]
python3 hub.py make-handout --exam-key 考试标识                       # Phase 2 已有
python3 hub.py make-handout --question-keys k1,k2 [--with-answer]      # 新增
python3 hub.py make-handout --recommend-for 学生UID [--limit N]        # 新增
```

API 新增（只读，沿用 `api.py` 约定，不回答案与解析）：

```
GET /api/questions?tag=&type=&difficulty=&q=
GET /api/questions/recommend?student_uid=
```

---

## 附录 B：自制示例题规范（执行者必读）

1. 全部原创：不得改编任何试卷、教辅、网络题目的题干、选项与答案；
   只允许用通用物理知识点（如「牛顿第二定律」「欧姆定律」）组织题目。
2. 数量 ≤15 道，覆盖 `choice` / `fill` / `calculation` / `experiment` 四种题型，难度 1–5 尽量拉开。
3. 每道题打 1–3 个知识点标签，标签必须在 `[question_bank] tags` 白名单内。
4. 题干纯文本，不含图片与复杂公式（公式用文字描述，如 `F=ma`）。
5. 原创声明与编写日期写在 `examples/README.md`（JSON 不能写注释）。
6. 示例题同样受隐私扫描约束：题干里不得出现真实人名、联系方式等；题集被 Git 跟踪，
   默认的 `privacy_scan.sh` 会扫到它。
7. 物理正确性由维护者过一遍再合入（决策三 A）。

---

## 修订记录

2026-09-26（维护者确认后定稿）：

1. 决策一 A：`phase2 → phase3` 走真迁移，新增 `hub.py upgrade-db`，已知旧版本不再要求重建；
   `db.py` / `init_db.py` 的版本语义随之修改（原稿只写了迁移文件，链路走不通）。
2. 决策二 A：自制示例题放顶层 `examples/`，不动 `data/` 的语义与
   `.gitignore` / `PRIVACY.md` / `privacy_scan.sh` / `test_repo_hygiene.py` 四道护栏。
3. 决策三 A：示例题由执行者起草，维护者审物理正确性后合入。
4. 评审保持单点 R4（不拆 R5）。
5. 原稿要求给 `.json` 写文件头注释——JSON 不支持注释，改为原创声明放 `examples/README.md`。
6. §7 隔离自查补齐第 4 条「接口隔离」，与 §1 的四条逐条对应。
7. 空题干/空答案的约束改为 `CHECK(length(trim(...)) > 0)`；`choice` 必须有 `options_json`；
   `difficulty` 明确允许 NULL（「未标难度」）。
8. `make-handout` 组卷讲义默认不含答案，且与现有 `handout_<exam_key>.md` 分开命名。
9. 补上 P3.3 的难度分档对照表；把「题号 ↔ 题库题目暂不关联」写进待定清单。
10. 修正文件引用（`tests/test_exam_chain.py`）与 PHASE2_PLAN 修订条目的引用措辞；
    补上原稿漏掉的 `config_loader.py`（`[question_bank]` 要落成配置字段才算数）。
11. 2026-09-26：维护者人工核对 12 道自制示例题的物理正确性，全部通过（决策三 A 闭环）。
    题集按附录 B 第 4 条保持纯文本、无配图（图片题不在 Phase 3 范围内，见 §1 非目标）。
