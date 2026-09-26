# 物理教学中枢（Physics Teaching Hub）

**给中学物理老师的本地教学数据中枢**：把课堂记录、作业与订正、错因、学生画像、考试分析、
题库与讲义放进同一个本地数据库，由命令行和一份可以双击打开的看板驱动。
**数据不出本机**，不需要服务器、不需要联网，只要 Python 3.11+。

> **状态：v0.1.0-alpha（早期版本）**。本仓库是作者私有教学系统的开源精简版，
> 功能会**长期落后于**内部版本，这是预期状态而不是缺陷；接口也会随搬运调整。
> 路线见 [docs/ROADMAP.md](docs/ROADMAP.md)，搬运进度与差异见 [docs/MIGRATION_LEDGER.md](docs/MIGRATION_LEDGER.md)。

---

## 30 秒上手（虚构演示数据，不装依赖、不改配置）

```bash
git clone https://github.com/kkxh/physics-teaching-hub.git
cd physics-teaching-hub

python3 hub.py init-db --demo           # 建库 + 写入虚构班级与学生
python3 hub.py import-scores --demo     # 导入虚构成绩与小题得分
python3 hub.py make-report              # 生成报告：outputs/phase1_report.md

# 想看看题库与看板：
python3 hub.py import-questions --demo  # 导入仓库自带的 12 道自制示例题
python3 hub.py list-questions           # 检索题目（答案默认隐藏）
python3 hub.py make-dashboard           # 生成单文件看板：outputs/dashboard/index.html
```

需要 Python 3.11 或更高版本（配置解析用标准库 `tomllib`）。上面这些命令**只用标准库**；
只有读 Excel `.xlsx` 成绩表时才需要 `python3 -m pip install -r requirements.txt`
（`openpyxl`，MIT，见 [docs/THIRD_PARTY_LICENSES.md](docs/THIRD_PARTY_LICENSES.md)）。

## 它解决什么问题

- **一个库装下整个学期**：成绩（CSV / Excel，含小题得分）、作业与订正、错因与行为记录，
  都进同一个 SQLite 文件，字段口径统一（比如完成率只按「作业所属班级的当前学生」算）。
- **从数据到下一步动作**：学生画像（成绩水平 / 作业习惯 / 错因控制 + 综合分）、
  预警与跟进闭环、周报、阶段巡检、考试逐题分析（得分率 / 难度 / 区分度）与讲评讲义。
- **题库只搬框架**：题目（题型 / 题干 / 选项 / 答案 / 解析 / 难度 / 知识点标签）自己导入，
  支持按标签、题型、难度、关键词检索，按错因标签 × 画像难度档推荐，并可组卷成讲义。
  仓库里只有 12 道**自制**示例题，使用者自己的题库永远留在本机。
- **能直接分享的产物**：报告与看板只写相对路径，可以拷给学生或同行看；看板是单文件 HTML，双击即开。

## 数据在哪里，隐私边界怎么划

- 数据库、演示数据、报告、看板都落在你的工作目录（默认 `data/`、`demo/`、`outputs/`），
  而且都被 `.gitignore` 拒绝——不会进版本库，也不会被上传。
- 本仓库**不含任何真实学生数据**（姓名、学号、成绩、班级、学校），
  也**不含任何第三方试卷、教辅或教材内容**；演示数据由 `seed_demo_data.py` 生成，示例题全部自制。
- 你自己的题库、成绩与课堂记录只应留在本机；提交任何东西前先跑 `bash scripts/privacy_scan.sh`。
- 程序本身不联网、无遥测、没有账号体系；唯一的联网场景是你自己 `pip install` 依赖。

细节见 [docs/PRIVACY.md](docs/PRIVACY.md)。

## 现在能做什么

- **配置外置**：学段、学科、学期、课表、班级、数据库路径都由 `config.toml` 决定，见下方「配置」。
- **文案外置**：学科名、学段名、阶段名、常用术语与演示提示语都放在 `labels/zh-CN.toml`，代码里只出现 key。
- **虚构演示数据生成器**：`seed_demo_data.py` 生成完全虚构的班级、学生、成绩、作业与示例题。
- **最小闭环**：`hub.py init-db --demo` → `import-scores --demo` → `make-report`。
- **导入**：成绩（CSV / Excel）、作业提交、小题得分、题库（JSON / CSV），都支持列名映射与 `--dry-run` 预览。
- **记录**：错因（标签字典可扩展）与行为记录；写操作走「预览 → `--yes` → 单事务」。
- **分析**：学生画像（支持增量重算）、作业完成率、考试逐题分析。
- **输出**：周报、阶段巡检、考试分析、讲评讲义（不含试卷原题）、题目讲义（默认不含答案）、可离线打开的本地看板。
- **题库**：检索（标签 / 题型 / 难度 / 关键词，答案默认隐藏）、推荐（错因标签 × 画像难度档，带可解释理由，纯只读）、组卷讲义；题目是使用者数据，仓库只带自制示例题。
- **升级**：`hub.py upgrade-db` 把存量 `phase2` 库非破坏式升到当前 schema（只跑迁移，不删库）。
- **本地 API**：只读 GET、只绑 `127.0.0.1`、CORS 白名单，见下方。
- **隐私扫描**：`scripts/privacy_scan.sh` 在提交前拦住真实数据、绝对路径与凭据痕迹，另有 `--history` 扫全部 Git 历史。
- **持续集成**：GitHub Actions 跑测试、演示数据自检与隐私扫描（Python 3.11 与 3.14）。

> 数据库 schema 现为 `phase3`。存量 `phase2` 库用 `python3 hub.py upgrade-db` 升级；
> Phase 1 的临时库（`phase1-temp`）不自动迁移，需要 `init-db --demo --rebuild --yes` 重建。

## 常用命令

`hub.py` 是统一入口：全局选项 `--config` / `--db` 放在子命令之前
（例如 `python3 hub.py --db /tmp/isolated.db make-report` 把读写指向一个隔离库）。
Phase 1 的三个脚本 `init_db.py` / `import_scores.py` / `make_report.py` 仍然可用，它们只是 `hub.py` 的兼容垫片。

导入自己的成绩表（列约定见 [data/README.md](data/README.md)）：

```bash
python3 hub.py import-scores --csv 成绩表.csv --exam 十月月考 --exam-date 2026-10-15 --dry-run
python3 hub.py import-scores --excel 成绩表.xlsx --sheet 成绩 --exam 十月月考 --exam-date 2026-10-15
```

读 `.xlsx` 需要 `openpyxl`：`python3 -m pip install -r requirements.txt`；其余功能只用标准库。

记错因与行为（写入前先给预览，确认后加 `--yes`）：

```bash
python3 hub.py record-error --student 高一(A)班-01 --tag calculation --exam-key demo-exam-1 --note "计算失误"
python3 hub.py list-errors --student 高一(A)班-01
python3 hub.py record-behavior --student 高一(A)班-01 --kind class_participation --detail "主动讲题" --yes
python3 hub.py homework-stats          # 作业完成率（分子分母都只算本班当前学生）
python3 hub.py compute-profile         # 学生画像（成绩水平 / 作业习惯 / 错因控制 + 综合分）
python3 hub.py scan-alerts             # 按 [alerts] 阈值扫描预警（幂等）
python3 hub.py resolve-alert 1 --note "已和家长沟通"   # 解决预警并留下跟进记录
python3 hub.py list-follow-ups --alert 1              # 查看某条预警的跟进记录
python3 hub.py weekly-report --week 2026-W45          # 某一周的周报（默认上一周）
python3 hub.py phase-patrol                           # 阶段巡检：进度 / 覆盖 / 预警 / 提醒
python3 hub.py exam-analysis --exam-key demo-exam-1    # 考试分析：逐题得分率/难度/区分度
python3 hub.py make-handout --exam-key demo-exam-1     # 讲评讲义（不含试卷原题）
python3 hub.py make-handout --question-keys k1,k2       # 题目讲义（按题库 key 组卷，默认不含答案）
python3 hub.py serve --port 8420                      # 本地只读 API（只绑 127.0.0.1）
python3 hub.py make-dashboard                         # 生成可离线双击打开的看板
python3 hub.py import-homework --csv 作业.csv --assign-key hw-01 \
    --class 高一(A)班 --topic "运动学图像" --assigned-date 2026-09-30   # 作业提交导入
python3 hub.py import-item-scores --csv 小题得分.csv --exam-key demo-exam-1  # 小题得分导入
python3 hub.py import-questions --demo                          # 导入仓库自带的 12 道自制示例题
python3 hub.py import-questions --json 题库.json --dry-run      # 预览导入自己的题库（JSON / CSV）
python3 hub.py list-questions --tag 欧姆定律 --limit 5           # 检索题目（答案默认隐藏，--show-answer 才显示）
python3 hub.py recommend-questions --student 高一(A)班-01        # 按错因与画像推荐题目（只读）
python3 hub.py make-handout --recommend-for 高一(A)班-01         # 按推荐组卷成题目讲义
python3 hub.py upgrade-db                                      # 存量 phase2 库非破坏式升级
python3 hub.py list-alerts --status open              # 列出预警
python3 hub.py alert-stats                            # 预警统计
```

本地 API 只做只读 GET，供看板前端或本地脚本查询：
`/api/meta`、`/api/classes`、`/api/exams`、`/api/exam/{exam_key}/stats`、
`/api/class/{班名}/averages`、`/api/homework/stats`、`/api/alerts?status=open|resolved|all`、
`/api/questions?tag=&type=&difficulty=&q=&limit=`（题库检索）、
`/api/questions/recommend?student_uid=&limit=`（题目推荐）——两个接口都不回答案与解析。
只绑定 `127.0.0.1`；CORS 只回固定的本机 Origin（含实际端口）并带 `Vary: Origin`，不使用通配符。

看板生成在 `outputs/dashboard/`：`index.html` 里**内嵌**了数据，双击就能看（不需要起服务、不引用任何 CDN）；
页面文字取自 `labels/zh-CN.toml` 的 `[dashboard]` 段，想改标题或列名改那里就行。

错因标签字典内置 5 类，可在 `config.toml` 的 `[error_tags]` 里改显示名或加自己的代码。

## 配置

复制 `config.example.toml` 为 `config.toml` 后按自己的情况修改；`config.toml` 不会进入 Git。
它是唯一事实来源，引擎不绑定学段、学科、班级与学校作息。

| 配置项 | 含义 | 可覆盖的环境变量 |
| --- | --- | --- |
| `project.name` | 项目名，进报告文案 | — |
| `project.locale` | 文案表语言，取 `labels/<locale>.toml` | — |
| `project.stage` | 学段：`high_school`（高中） / `middle_school`（初中） | `PHYSICS_TEACHING_STAGE` |
| `project.subject` | 学科标识，默认 `physics` | — |
| `project.timezone` | IANA 时区，如 `Asia/Shanghai`；教学日与教学周按此时区计算 | `PHYSICS_TEACHING_TIMEZONE` |
| `paths.database` | 数据库路径 | `PHYSICS_TEACHING_DATABASE` |
| `paths.output_dir` | 报告与导出目录（相对路径规则同左） | `PHYSICS_TEACHING_OUTPUT_DIR` |
| `semester.name` | 学期名，进报告文案 | — |
| `semester.starts_on` / `semester.ends_on` | 学期起止日期，必填 | — |
| `classes.names` | 班级名列表 | — |
| `schedule.starts_on` | 课表起点，省略时取学期起点 | — |
| `schedule.weekdays` | 上课日，1=周一 … 7=周日 | — |
| `schedule.periods` | 节次标签，供演示课表使用 | — |
| `demo.seed` | 演示数据的随机种子，同一份配置永远生成同一份数据 | — |
| `demo.students_per_class` | 演示数据每班人数 | — |
| `demo.output` | 演示数据落盘位置，默认 `demo/demo_dataset.json` | — |
| `question_bank.tags` | 知识点标签白名单，题库导入只接受这里的标签 | — |
| `question_bank.tag_map` | 错因标签 → 知识点标签的映射（推荐题目时用） | — |

几点约定：

- 环境变量只认上表列出的几个名字；白名单之外的 `PHYSICS_TEACHING_*` 一律忽略，避免配置来源不可追踪。
- 换配置文件位置用 `PHYSICS_TEACHING_CONFIG=/path/to/config.toml`。
- 配置不合法会直接报错并指出字段名：学段、时区、日期格式、学期起止先后、课表起点晚于学期结束、空班名、重复班名、上课日取值等都会被拦下。

### 路径与时间

- 配置里的相对路径一律以「配置文件所在目录」为基准解析，跟你在哪个目录敲命令无关；`~` 展开为家目录，绝对路径原样使用。
- 数据库目录与输出目录不存在时，首次运行会自动创建。使用者数据的存放约定见 [data/README.md](data/README.md)。
- 一周从周一开始：`schedule.starts_on` 所在的那一周是第 1 教学周。学期范围之外的日期不算教学周；学期已开始但还没到课表起点时记为第 0 周（未开课）。
- 上课日由 `schedule.weekdays` 决定（1=周一 … 7=周日）；「今天」按 `project.timezone` 计算，不依赖机器本地时区。
- 不处理节假日与调休。

### 教学阶段

- 不配置阶段时，按**学段 profile** 的默认阶段把学期切开：初中是「新授课 → 单元复习 → 中考复习」，高中是「新授课 → 一轮复习 → 二轮专题 → 考前冲刺」，最后一个阶段吸收余下的尾周。
- 想按自己的复习节奏来，就用 `[[phases]]` 整体覆盖默认值：

```toml
[[phases]]
name = "新授课"
starts_on = "2026-09-01"
ends_on = "2026-11-06"
```

- 约束：阶段按时间顺序排列、首尾相接、不重叠不留缺口，并完整覆盖学期；阶段名不能为空或重复。违反会直接报错，错误信息会点出是哪两个阶段冲突。

### 文案

- 面向使用者的文字集中在 [labels/zh-CN.toml](labels/zh-CN.toml)：学科名、学段名、阶段名、学期/教学周/作业/订正/错因等术语，以及演示数据的提示语。代码里只出现 key（如 `phases.new_lesson`）。
- `[project] locale` 决定取哪一份文案表；想换成自己的说法，复制一份改名（例如 `my-zh.toml`），再用 `[project] labels_dir` 指向它所在的目录（相对路径按配置文件所在目录解析）。
- 缺 key、locale 文件不存在、文案不是字符串或写成空串，都会直接报错，错误信息带上文件路径与 key 名——不会静默显示空白。

### 演示数据

- `seed_demo_data.py` 按配置生成虚构数据：班级取 `[classes] names`，人数、种子、考试、作业主题与落盘位置取 `[demo]`。
- 配置文件按「`--config` → `PHYSICS_TEACHING_CONFIG` → 当前目录的 `config.toml` → 仓库自带的 `config.example.toml`」的顺序找；全新 clone 不动配置也能先跑起来看效果。
- 考试与作业日期按学期进度自动落点（`[[demo.exams]]` 的 `progress` 是 0~1 的学期进度），永远落在 `[semester]` 范围内，不用手改日期。
- 生成结果只写在本机（默认 `demo/`，已在 `.gitignore` 里）；姓名一律是「前缀 + 两位序号」的虚构样式，自检会拦住不符合模式的姓名。

## 常见问题

**数据会上传吗？** 不会。程序不联网、没有服务端、没有遥测；所有数据只写在你自己机器上的 SQLite 文件与输出目录里。

**要联网吗？** 运行不需要。只有想用 Excel 导入时要自己 `pip install` 一次依赖；用 CSV 则完全不用装东西。

**我的成绩和题库会被公开吗？** 不会。仓库里没有任何使用者数据；数据库、导入文件、输出物都在 `.gitignore` 的拒绝清单里。你自己的题库放哪里由你决定，只要别提交进 Git。

**我能导入自己的试卷吗？** 能，但只能导入**你有权使用**的内容；仓库不附带也不分发任何第三方试卷、教辅或教材内容，当前也不解析 PDF 与图片题（题面是纯文本）。

**支持初中吗？** 支持。`[project] stage` 设成 `middle_school`，阶段与文案就会换成初中那一套；班级、学期、课表都由配置决定。

**为什么功能看起来不多？** 因为它先是作者自己在用的工具，公开的是精简版；上游私仓不会同步到本仓库，功能长期落后是预期状态。

## 测试与自检

```bash
python3 -m compileall -q .
python3 -m unittest discover -s tests -t .   # 测试只用临时库与虚构数据，不碰真实数据
TZ=UTC python3 -m unittest discover -s tests -t .
python3 seed_demo_data.py --check            # 演示数据自检
bash scripts/privacy_scan.sh                 # 隐私扫描（提交前）
bash scripts/privacy_scan.sh --all
bash scripts/privacy_scan.sh --history       # 扫全部 Git 历史（发布前审计用）
```

## 不做什么（非目标）

- 多用户、权限、云端同步：不做。这是个**单机工具**。
- 在线题库与题库同步、图片题与公式渲染：不做（题面纯文本）。
- PDF / Word 题目导入：待定，不承诺。
- 英文文档、视频教程：暂不做。
- 功能追赶上游：不追，见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 长期路线与参与

- 路线与阶段：[docs/ROADMAP.md](docs/ROADMAP.md)；搬运进度与与上游的差异：[docs/MIGRATION_LEDGER.md](docs/MIGRATION_LEDGER.md)。
- 想提 PR 请先看 [CONTRIBUTING.md](CONTRIBUTING.md)（隐私红线是硬门槛）；版本变化见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证与免责声明

MIT License，见 [LICENSE](LICENSE)。项目名称与作者署名归作者所有；MIT 不授予商标权。

本工具用于辅助教学记录与分析，输出结果不构成教育评价结论，使用前请自行核对。
