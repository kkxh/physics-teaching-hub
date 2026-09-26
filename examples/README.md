# examples/：自制示例题集

这个目录放**本仓库自制的**示例题，用来演示题库导入、检索、推荐与组卷的用法。
使用者自己的题库不放这里（见文末）。

## 内容说明

- `questions_demo.json`：12 道自制示例题，纯 JSON 数组，题面、选项、答案、解析全部原创，
  编写日期 2026-09-26，按本仓库的 [LICENSE](../LICENSE) 分发。
- 覆盖 `choice`（选择）、`fill`（填空）、`calculation`（计算）、`experiment`（实验）四种题型，
  难度 1–5 都有，每题 1–2 个知识点标签，标签取自 `config.example.toml` 的
  `[question_bank] tags` 白名单。
- 这里**没有**任何试卷原题、教辅题目或教材节选，也不含图片。

## 怎么导入

```bash
python3 hub.py import-questions --json examples/questions_demo.json --dry-run   # 先预览
python3 hub.py import-questions --json examples/questions_demo.json             # 真正写入
python3 hub.py import-questions --demo                                          # 等价：从演示数据集取这套示例题
```

`--demo` 是从 `seed_demo_data.py` 生成的演示数据集里取题（内容与 `questions_demo.json` 一致），
`--json` 走的是普通导入路径——两种方式都能验证格式。

## JSON 格式

文件是一个数组，每个元素是一道题：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `question_key` | 是 | 题目稳定标识，全库唯一，重复导入会整批中止 |
| `qtype` | 是 | `choice` / `fill` / `calculation` / `experiment` / `other` |
| `stem` | 是 | 题干（纯文本，不含图片） |
| `answer` | 是 | 答案 |
| `options` | 选择题必填 | 选项数组；非选择题可省略 |
| `analysis` | 否 | 解析 |
| `difficulty` | 否 | 1–5；省略表示「未标难度」（不是 0） |
| `source_label` | 否 | 来源说明；自制示例题统一写「自制示例」 |
| `tags` | 是 | 知识点标签数组，必须在 `[question_bank] tags` 白名单里 |

CSV 用同一套字段名，`options` 与 `tags` 里多个值用竖线 `|` 分隔，表头不一样时用 `--columns` 映射。

```csv
question_key,qtype,stem,answer,options,difficulty,tags
demo-1,choice,示例题干,B,A. 甲|B. 乙,2,欧姆定律|串并联电路
```

## 版权与隐私边界

- 只能导入你自己有权使用的内容；不要把试卷原题、教辅题目、教材节选导入或提交进仓库。
- 你自己的题库是使用者数据，放在 `data/`（`.gitignore` 已拒绝）或本机任意位置，**不要入 Git**。
- 题干里不要写真实学生姓名、班级或联系方式；提交前跑 `bash scripts/privacy_scan.sh`。
