# 参与贡献

先谢谢你愿意花时间。这是一个**单机、本地优先**的教学工具，作者业余维护，
所以流程尽量轻，但隐私这条线不能碰。

## 隐私红线（硬门槛，违反直接关闭 PR）

**不接受**任何包含下列内容的提交，包括测试夹具、示例文件、截图与提交信息：

- 真实学生信息：姓名、学号、座位号、成绩、作业状态、行为或跟进记录；
- 真实班级标识、真实学校名称、地区教研专有说法、教师个人信息；
- 第三方内容：试卷原题、教辅题目、教材节选、题目图片、讲评媒体；
- 密钥、令牌、`.env`、SSH 材料，以及任何形式的**本机绝对路径**。

本仓库只接受**代码 + 虚构演示数据**。演示数据用 `python3 seed_demo_data.py` 生成，
示例题必须是自制的（参考 `examples/questions_demo.json`）。

提交前请跑：

```bash
python3 -m compileall -q .
python3 -m unittest discover -s tests -t .
TZ=UTC python3 -m unittest discover -s tests -t .
python3 seed_demo_data.py --check
bash scripts/privacy_scan.sh
bash scripts/privacy_scan.sh --all
```

涉及历史提交的改动（极少见）另外跑 `bash scripts/privacy_scan.sh --history`。

## 代码约定

动手前请读：

1. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) —— 分层、依赖方向、数据流；
2. [docs/ENGINEERING_NOTES.md](docs/ENGINEERING_NOTES.md) —— 14 条踩坑约定（身份只认 ID、
   时间统一 UTC、写入单事务、判空用 `is None`、用户输入不做 LIKE 通配符、检查脚本跑不成必须报错……）；
3. [docs/PRIVACY.md](docs/PRIVACY.md) —— 边界清单；
4. [AGENTS.md](AGENTS.md) —— 本仓库的硬规则与模块搬运完成定义。

几条最常被守护测试打回的：

- 数据访问必须走 `db.connect`（除 `db.py` 外不允许出现 `sqlite3.connect(`）；
- 面向用户的文字放进 `labels/zh-CN.toml`，代码里只出现 key；
- schema 变更走 `schema/migrations/`，并同时更新新建库用的 `schema/*.sql`；
- 新模块要加进 `tests/test_labels.py` 的扫描清单；
- 破坏性操作要有二次确认，且**显式开关必须真的生效**。

## 提 PR 的方式

1. 一个 PR 只做一件事（一个模块或一个明确的问题）；
2. 说明「改了什么、为什么、怎么验证的」；
3. 附上本地自检结果；
4. 如果是功能，请顺带补测试（主路径 + 至少一个边界）。

## 维护者声明

- 这是**业余维护**的项目：不承诺响应时效，也不承诺接受所有功能建议；
- **不追上游**：作者的私有版本不会同步到本仓库，功能长期落后是预期状态；
- 与作者私有系统相关的问题、内部文档、课堂内容不会出现在这里，也请不要询问。

## 许可

你的贡献将按本仓库的 [MIT License](LICENSE) 分发。
