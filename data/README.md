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

### Excel（.xlsx）

```bash
python3 -m pip install -r requirements.txt        # 需要 openpyxl
python3 hub.py import-scores --excel 成绩表.xlsx --sheet 成绩 \
    --exam 考试名 --exam-date 2026-11-05 \
    --columns "学号=student_uid,姓名=name,分数=score"
```

- 列约定、分数校验、`--dry-run`、幂等规则与 CSV 完全一致；默认读**第一个工作表**，用 `--sheet` 指定别的。
- 空白单元格视为「没有分数」，会被拒绝；Excel 末尾的整行空行会自动忽略。
- 只读 `.xlsx`；老的 `.xls` 请先另存为 `.xlsx`。

## 作业提交表（CSV）约定

```bash
python3 hub.py import-homework --csv 作业.csv --assign-key hw-01 \
    --class 高一(A)班 --topic "运动学图像" --assigned-date 2026-09-30 --due-date 2026-10-02
python3 hub.py homework-stats [--class 高一(A)班]
```

- 表头默认认：`student_uid`（学号，推荐）、`name`（姓名）、`status`（状态）、
  `submitted_on`（提交日期）、`corrected_on`（订正日期）、`note`（订正备注）；后三列可缺。
- `status` 只能是 `submitted`（已交）/ `late`（迟交）/ `missing`（缺交），其余值会被拒绝。
- 学生标识规则同成绩表：给了 `student_uid` 就按它匹配，只给姓名时重名会报错。
- `corrected_on` 或 `note` 填了就记一条订正；`missing` 的行不许带订正。
- `--assign-key` 是这次作业的稳定标识：重复导入同一份作业继续用它，改主题或日期会就地更新；
  同一个 key 换班级会被拒绝（防止把 A 班的作业悄悄挪到 B 班）。
- **完成率口径**：完成率 = 已交 / 应交，分子与分母都只算「作业所属班级的**当前**学生」；
  转班学生的历史提交不计入任何班级（否则完成率会超过 100%）。缺交 = 应交 − 已交；迟交单列；
  订正率 = 有订正的已交记录 / 已交记录。
- Phase 2 不做历史名册：应交按「当前名册 × 作业份数」回算，中途插班的学生会算进更早的作业。
