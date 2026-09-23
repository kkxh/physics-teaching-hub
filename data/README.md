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

## 成绩表（CSV）约定

```bash
python3 hub.py import-scores --csv 成绩表.csv --exam 考试名 --exam-date 2026-11-05
```

- 编码 UTF-8（带不带 BOM 都行），第一行是表头。
- 表头默认认这三列：`student_uid`（学号，推荐）、`name`（姓名）、`score`（分数）。
- **学生标识**：`student_uid` 与 `name` 至少给一列。给了 `student_uid` 就按它精确匹配，姓名只用于显示；
  只给姓名时，匹配到 0 人或多人都会报错——重名必须在表里写学号。
- **分数**：空值、非数字、负数都会被拒绝；`0` 是合法成绩。任何一行有问题都会让整批导入中止，不会写一半。
- 表头不一样时用映射，例如 `--columns "学号=student_uid,姓名=name,分数=score"`。
- 先用 `--dry-run` 预览：会说明考试是新建还是沿用、待写入多少条、涉及多少名学生。
- 同一场考试重复导入是幂等的（按「考试名 + 日期 + 学生」覆盖分数）；
  **日期不同就是另一场考试**——所以同一次考试的成绩导入与错题导入要用同一个 `--exam-date`。
- 导入只写本机数据库（路径见 `config.toml` 的 `paths.database`），不联网、不导出。
