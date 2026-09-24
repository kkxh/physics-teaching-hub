# 物理教学中枢（Physics Teaching Hub）

面向中学物理教师的本地教学数据中枢：把课堂记录、作业与订正、错因、学生画像、阶段巡检和练习推荐放进同一个本地数据库，由命令行与本地看板驱动，数据不出本机。

> **当前状态：v0.0.1，Phase 1（通用化地基）已完成。**
> 本仓库是作者私有教学系统的开源精简版，正在按 [docs/ROADMAP.md](docs/ROADMAP.md) 逐步搬运通用能力。
> 功能会**长期落后于**内部版本，这是预期状态，不是缺陷；接口也会随搬运调整。

---

## 现在能做什么

Phase 1 的通用化地基已经能跑通一条最小闭环——**建库 → 导入成绩 → 生成报告**，全部使用虚构演示数据：

- **最小闭环**：`init_db.py --demo` → `import_scores.py` → `make_report.py`，产出 `outputs/phase1_report.md`（含学期、教学周、教学阶段与考试统计）。
- **配置外置**：学段、学科、学期、课表、班级、数据库路径都由 `config.toml` 决定，见下方「配置」。
- **文案外置**：学科名、学段名、阶段名、常用术语与演示提示语都放在 `labels/zh-CN.toml`，代码里只出现 key，见下方「文案」。
- **虚构演示数据生成器**：`seed_demo_data.py` 生成完全虚构的班级、学生、成绩与作业记录。
- **隐私扫描**：`scripts/privacy_scan.sh` 在提交前拦住真实数据、绝对路径与凭据痕迹。
- **持续集成**：GitHub Actions 跑测试、演示数据自检与隐私扫描。

> 提醒：最小闭环用的数据库 schema 是**临时**的（`meta.schema_version = 'phase1-temp'`），
> Phase 2 会用正式的模块化 schema 替换或扩展，表名与字段不承诺兼容。

## 学会的边界（重要）

- 本仓库**不包含任何真实学生数据**：没有真实姓名、成绩、课堂记录、班级或学校标识。
- 本仓库**不包含任何第三方教辅、教材、试卷或题目图片**。示例题必须自制。
- 演示数据全部来自 `seed_demo_data.py`，可以随时重新生成、随时删除。

细节见 [docs/PRIVACY.md](docs/PRIVACY.md)。

## 快速开始

```bash
git clone https://github.com/kkxh/physics-teaching-hub.git
cd physics-teaching-hub

# 三条命令跑通最小闭环（虚构演示数据；没复制配置也能跑，会用 config.example.toml）
python3 hub.py init-db --demo                # 建库 + 灌入虚构名单
python3 hub.py import-scores --demo          # 导入演示成绩
python3 hub.py make-report                   # 生成报告：outputs/phase1_report.md

# 自检
python3 -m unittest discover -s tests -t .   # 跑测试
python3 seed_demo_data.py                    # 生成虚构演示数据（没 config.toml 时用示例配置）
python3 seed_demo_data.py --check            # 演示数据自检
bash scripts/privacy_scan.sh                 # 隐私扫描
```

需要 Python 3.11 或更高版本（配置解析使用标准库 `tomllib`）；最小闭环只用标准库，不需要安装依赖。

`hub.py` 是 Phase 2 起的统一入口：全局选项 `--config` / `--db` 放在子命令之前
（例如 `python3 hub.py --db /tmp/isolated.db make-report` 可以把读写指向一个隔离库）。
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
```

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

几点约定：

- 环境变量只认上表列出的几个名字；白名单之外的 `PHYSICS_TEACHING_*` 一律忽略，避免配置来源不可追踪。
- 换配置文件位置用 `PHYSICS_TEACHING_CONFIG=/path/to/config.toml`（Phase 1 后续命令会补 `--config` 参数）。
- 配置不合法会直接报错并指出字段名：学段、时区、日期格式、学期起止先后、课表起点晚于学期结束、空班名、重复班名、上课日取值等都会被拦下。

### 路径与时间

- 配置里的相对路径一律以「配置文件所在目录」为基准解析，跟你在哪个目录敲命令无关；`~` 展开为家目录，绝对路径原样使用。
- 数据库目录与输出目录不存在时，首次运行会自动创建。使用者数据的存放约定见 [data/README.md](data/README.md)。
- 一周从周一开始：`schedule.starts_on` 所在的那一周是第 1 教学周。学期范围之外的日期不算教学周；学期已开始但还没到课表起点时记为第 0 周（未开课）。
- 上课日由 `schedule.weekdays` 决定（1=周一 … 7=周日）；「今天」按 `project.timezone` 计算，不依赖机器本地时区。
- Phase 1 不处理节假日与调休。

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

## 长期路线

见 [docs/ROADMAP.md](docs/ROADMAP.md)。搬运进度记录在 [docs/MIGRATION_LEDGER.md](docs/MIGRATION_LEDGER.md)。

## 许可证与免责声明

MIT License，见 [LICENSE](LICENSE)。项目名称与作者署名归作者所有；MIT 不授予商标权。

本工具用于辅助教学记录与分析，输出结果不构成教育评价结论，使用前请自行核对。
