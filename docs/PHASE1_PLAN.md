# Phase 1 小步清单：通用化地基

> 状态：**进行中**——P1.1、P1.2 已完成（2026-09-21）。每完成一步，就把对应小节标题里的状态补上并写日期。
> 范围锚点：[ROADMAP.md](ROADMAP.md) 的 Phase 1。本文件只细化步骤，不扩大范围。

## 执行约定

- 一条分支 `codex/phase1-foundation`，**每步一个提交**；提交信息写清「做了什么 + 与上游的差异」。
- 每步提交前全绿：`python3 -m unittest discover -s tests -t .` 与 `bash scripts/privacy_scan.sh`；CI 另外跑编译检查与演示数据自检。
- 沿用平铺模块结构，**不先做包结构重构**；不新增第三方运行时依赖（只用标准库）。
- 不引入真实数据、第三方内容与维护者本机的绝对路径；本文件同样受这条约束。

## 已确认的决定

1. 最小闭环用**三个独立脚本**（`init_db.py`、`import_scores.py`、`make_report.py`），Phase 2 再考虑收成统一 CLI。
2. 配置分 `[semester]`（学期范围）与 `[schedule]`（课表起点与周次）两个小节。
3. 本清单随 P1.1 的提交一起进入仓库。

---

## P1.1 配置全覆盖（已完成 2026-09-21）

目标：`config.toml` 成为唯一事实来源——学期、课表起点、周次、时区、班级、数据库与输出路径全部走配置；环境变量只开放固定白名单。

验收：

- 白名单变量（`PHYSICS_TEACHING_CONFIG`、`_STAGE`、`_DATABASE`、`_OUTPUT_DIR`、`_TIMEZONE`）各自覆盖成功；白名单外的 `PHYSICS_TEACHING_*` 一律无效。
- 非法值统一报 `ConfigError` 并指出字段名：学段、时区、日期格式、学期起止先后、上课日取值、空班名、重复班名、重复节次。
- `config.example.toml` 自身可解析；缺配置文件时的报错仍指向示例配置。
- README「配置」小节逐字段说明含义与对应环境变量，且与示例配置同步（有单测）。

改动文件：`config_loader.py`、`config.example.toml`、`tests/test_config.py`、`tests/test_smoke.py`、`README.md`、`docs/PHASE1_PLAN.md`。

## P1.2 路径与时间（已完成 2026-09-21）

目标：消除对工作目录的隐含依赖——所有路径相对**配置文件所在目录**解析；教学周计算独立成 `teaching_calendar.py`（周一为一周起点，课表起点所在周为第 1 周）；「今天」按时区取。

验收：

- `conf/config.toml` 里写 `database = "data/x.db"`，解析结果是 `<配置文件目录>/data/x.db`，与当前工作目录无关。
- 使用者手写的绝对路径保持原样，不被改写。
- 以 `starts_on = 2026-09-01`（周二）为例：9-01 → 第 1 周、9-06（周日）→ 第 1 周、9-07 → 第 2 周；学期起止之外的日期返回「不在学期内」。
- `weekdays = [1, 3, 5]` 时周三算教学日、周二不算。
- 时间函数接收明确的日期参数，测试不依赖「跑测试那天」。
- 补齐 `data/README.md`（`.gitignore` 已引用但文件尚不存在），并验证首次运行会自动创建父目录。

改动文件：`config_loader.py`、`teaching_calendar.py`（新增）、`data/README.md`（新增）、`config.example.toml`、`tests/test_config.py`、`tests/test_teaching_calendar.py`（新增）、`README.md`。

## P1.3 学段与教学阶段 profile（未开始）

目标：把「教学跑道」抽成可配置的阶段定义，学段决定默认 profile（新增 `stage_profiles.py`）；配置里的 `[[phases]]` 可整体覆盖默认。

验收：

- 两个学段的默认阶段都非空、名称唯一、首尾相接、完整落在学期范围内。
- 配置覆盖生效；`current_phase(date)` 在阶段起止当天（含）返回该阶段，前后一天判断正确。
- 非法阶段定义（重叠、缺口、越界、空名）报 `ConfigError`，错误信息点出冲突的两个阶段。
- 两个学段的默认阶段明显不同，且差异来自 profile 而不是散落代码。

改动文件：`stage_profiles.py`（新增）、`config_loader.py`、`config.example.toml`、`tests/test_stage_profiles.py`（新增）、`README.md`。

## P1.4 学科与学段文案收口（未开始）

目标：用户可见文案集中到 `labels/zh-CN.toml`，由 `labels.py` 读取；`config.toml` 的 `[project] locale` 决定取哪一份；清掉 `config_loader.STAGE_LABELS` 这类散落硬编码。

验收：

- labels 覆盖学科名、学段名、阶段名、学期/教学周/作业/订正/错因等词条；缺键时报错含文件路径与键名。
- 换一份 labels 测试夹具后，演示数据与报告里的对应文字随之变化（证明没有硬编码）。
- 用标准库 `tokenize` 扫描指定模块的字符串字面量，除 docstring 与错误信息白名单外不得出现「物理」「高中」「初中」。
- 代码里不再直接引用 `STAGE_LABELS`。

改动文件：`labels/zh-CN.toml`（新增）、`labels.py`（新增）、`config_loader.py`、`config.example.toml`、`seed_demo_data.py`、`tests/test_labels.py`（新增）。

## P1.5 演示数据配置化（未开始）

目标：演示数据完全由配置驱动（班级、人数、种子、考试、作业、输出路径、学期范围），保持确定性；演示通道强制虚构姓名。

验收：

- 同配置同种子两次生成逐字节一致（数据相等 + 落盘文件哈希相同）。
- 换成 `middle_school` + 虚构班名 + 20 人后，班级名、人数、日期随配置变化且自检通过。
- `--check` 仍然不落盘；输出路径的解析规则与 P1.2 一致。
- 人数与配置不符、或姓名不符合虚构模式时，自检返回问题清单并以非零退出。
- 演示数据的日期全部落在 `[semester]` 范围内。

改动文件：`seed_demo_data.py`、`config_loader.py`（新增 `[demo]`）、`config.example.toml`、`tests/test_demo_data.py`、`tests/test_smoke.py`、`README.md`。

## P1.6 首次可运行闭环（未开始）

目标：三条命令跑通最小闭环——`python3 init_db.py --demo`（建库 + 灌入虚构名单）→ `python3 import_scores.py`（导入虚构成绩）→ `python3 make_report.py`（把 Markdown 报告写到 `outputs/`）。

> **临时 schema 声明**：本步只落一个**临时** schema（`schema/phase1_schema.sql`，`meta` 表里 `schema_version = 'phase1-temp'`）。Phase 2 会用正式的模块化 schema 替换或扩展它，**表名与字段不承诺兼容，请勿据此做长期集成或数据迁移**。这句声明必须同时出现在 schema 文件顶部注释、本文件与 `docs/ROADMAP.md`。

验收：

- 端到端测试在临时目录生成配置后依次执行三条命令：退出码为 0，`data/*.db` 与 `outputs/*.md` 存在，报告含配置里的班级名/学期名/教学周，且统计值与直接查库结果一致。
- 重复执行三条命令不报错、不产生重复行（第二次执行后行数不变）。
- 名单里出现非虚构姓名时命令以非零退出，并提示「Phase 1 仅支持虚构演示数据」。
- `requirements.txt` 保持为空（只用标准库），CI 从零安装即可跑通。
- README 快速开始改成这三条命令，照抄即可跑通。

改动文件：`init_db.py`、`import_scores.py`、`make_report.py`（三个新增）、`schema/phase1_schema.sql`（新增）、`tests/test_phase1_loop.py`（新增）、`README.md`、`docs/ROADMAP.md`、`docs/MIGRATION_LEDGER.md`。

## P1.7 收口（未开始）

目标：把 Phase 1 收干净，并证明「别人 clone 或解包一份归档就能跑」。

验收：

- `git archive HEAD` 解包到临时目录后，照跑三条命令成功——不依赖任何未跟踪文件。
- 隐私扫描补 `data/` 目录规则（`data/README.md` 放行）；在 `data/` 放一个散落文件后扫描失败，删掉后通过。
- 三条命令跑完、工作区里确实有 `.db`/`.json`/报告时，扫描仍通过。
- 文档收口：README 能力现状、ROADMAP 顶部状态与 Phase 1 临时 schema 说明、台账状态与差异、隐私文档补一条「生成物只在本机」。
- 四条检查全绿：编译检查、单元测试、演示数据自检、隐私扫描；CI 绿。

改动文件：`README.md`、`docs/ROADMAP.md`、`docs/MIGRATION_LEDGER.md`、`docs/PRIVACY.md`、`scripts/privacy_scan.sh`、`tests/test_repo_hygiene.py`（新增）。

---

## 范围对齐

ROADMAP 的 Phase 1 五项与本文件小步的对应关系：①→P1.1、②→P1.2、③→P1.3、④→P1.4、⑤→P1.5 + P1.6；P1.7 只是收口，不新增功能。

## Phase 1 不做的事

真实成绩的通用导入格式；考试链路、题库、学生画像、预警跟进、前端与本地 API；多学段自动识别；节假日与调休。这些留给 Phase 2 及以后。

## 说明

上游私仓与上游本地目录不写进本文件，也不写进本仓库任何文件；本清单里的所有示例数据都是虚构的。
