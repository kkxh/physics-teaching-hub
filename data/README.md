# data/：使用者自己的数据

这个目录留给**使用者的**数据库与导入文件。它不在版本库里：

- `data/*` 已被 `.gitignore` 拒绝，只有本文件（`data/README.md`）例外；
- 仓库里没有任何真实数据；演示数据由 `seed_demo_data.py` 生成，故意不进 Git。

## 目录约定

| 路径 | 用途 | 入 Git |
| --- | --- | --- |
| `data/<name>.db` | SQLite 数据库，默认 `data/physics_teaching.db` | 否 |
| `data/imports/` | 使用者自己的导入文件（成绩表等） | 否 |
| `data/README.md` | 本文件 | 是 |

具体路径由 `config.toml` 的 `paths.database` 决定；相对路径以 `config.toml` 所在目录为基准解析。
目录不存在时，首次运行时由程序自动创建。

## 边界

不要把真实学生数据、真实班级标识或第三方试卷内容提交进仓库——即使它们曾经放在这个目录里。
提交前先跑 `bash scripts/privacy_scan.sh`。
