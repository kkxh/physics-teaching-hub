# 物理教学中枢（Physics Teaching Hub）

面向中学物理教师的本地教学数据中枢：把课堂记录、作业与订正、错因、学生画像、阶段巡检和练习推荐放进同一个本地数据库，由命令行与本地看板驱动，数据不出本机。

> **当前状态：v0.0.1，骨架阶段。**
> 本仓库是作者私有教学系统的开源精简版，正在按 [docs/ROADMAP.md](docs/ROADMAP.md) 逐步搬运通用能力。
> 功能会**长期落后于**内部版本，这是预期状态，不是缺陷；接口也会随搬运调整。

---

## 现在能做什么

骨架阶段（Phase 0）只包含工程约定，还没有教学功能：

- **配置外置**：学段、学科、班级、数据库路径都由 `config.toml` 决定，见下方「学段」。
- **虚构演示数据生成器**：`seed_demo_data.py` 生成完全虚构的班级、学生、成绩与作业记录。
- **隐私扫描**：`scripts/privacy_scan.sh` 在提交前拦住真实数据、绝对路径与凭据痕迹。
- **持续集成**：GitHub Actions 跑测试、演示数据自检与隐私扫描。

## 学会的边界（重要）

- 本仓库**不包含任何真实学生数据**：没有真实姓名、成绩、课堂记录、班级或学校标识。
- 本仓库**不包含任何第三方教辅、教材、试卷或题目图片**。示例题必须自制。
- 演示数据全部来自 `seed_demo_data.py`，可以随时重新生成、随时删除。

细节见 [docs/PRIVACY.md](docs/PRIVACY.md)。

## 快速开始

```bash
git clone https://github.com/kkxh/physics-teaching-hub.git
cd physics-teaching-hub

python3 -m unittest discover -s tests -t .   # 跑测试
python3 seed_demo_data.py                    # 生成虚构演示数据（demo/，不入 Git）
python3 seed_demo_data.py --check            # 演示数据自检
bash scripts/privacy_scan.sh                 # 隐私扫描
```

需要 Python 3.11 或更高版本（配置解析使用标准库 `tomllib`）。

## 学段：初中还是高中

引擎不绑定学段。`config.example.toml` 里的 `stage` 决定学段文案与阶段定义，目前预留两个取值：

```toml
[project]
stage = "high_school"   # high_school（高中） | middle_school（初中）
```

复制 `config.example.toml` 为 `config.toml` 后按自己的情况修改；`config.toml` 不会进入 Git。
Phase 1 会把班级、学期、课表起点、时区等一并接进来。

## 长期路线

见 [docs/ROADMAP.md](docs/ROADMAP.md)。搬运进度记录在 [docs/MIGRATION_LEDGER.md](docs/MIGRATION_LEDGER.md)。

## 许可证与免责声明

MIT License，见 [LICENSE](LICENSE)。项目名称与作者署名归作者所有；MIT 不授予商标权。

本工具用于辅助教学记录与分析，输出结果不构成教育评价结论，使用前请自行核对。
