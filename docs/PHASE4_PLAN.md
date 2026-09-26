# Phase 4 详细计划：文档与发布

> 状态：**已定稿（2026-09-26）**——维护者已确认四条决策（见 §0.3）。Phase 3 已于 2026-09-26 收口，R4 闭环。
> 范围锚点：[ROADMAP.md](ROADMAP.md) 的 Phase 4。本文件只细化步骤，不扩大范围。
> 本文件在 muse 的初稿基础上定稿：保留其结构与边界，修正 7 处与仓库现状不符或缺失的地方
> （见文末「修订记录」）。

---

## 0. 给执行者（Codex）的一页纸

### 0.1 开工前必读（按顺序）

1. `docs/ROADMAP.md` —— 确认 Phase 4 在路线中的位置与「发布前硬性待办」
2. `docs/MIGRATION_LEDGER.md` —— 台账现状（Phase 3 全部「已搬运」）
3. `docs/PRIVACY.md` —— 隐私与版权边界（本阶段最高优先级）
4. `docs/ENGINEERING_NOTES.md` —— 14 条工程约定
5. 本文件 —— 按步骤执行

### 0.2 最高约束（不可绕过）

1. **转公开、打 tag、发 Release，每一步都要维护者明确批准**（AGENTS.md 第 3.7 条、ROADMAP）。
2. **发布前必须对 git 全历史跑 `bash scripts/privacy_scan.sh --history`**（2026-09-26 新增的能力），
   不通过不发布；证据要贴进 R5 评审材料。
3. 推广文案不出现真实学校、真实人名；对外身份只用 `kkxh` / `DW YE`（决策二 A，锁死）；
   LICENSE 的 `DW YE` 署名是底线，不再多暴露。
4. 外部动作（转公开、tag、Release、社区发帖、GitHub topics / social preview）由**维护者执行**；
   Codex 只准备材料与文案，不代为点击发布。

### 0.3 已确认的四条决策（2026-09-26）

| 决策 | 结论 | 落地影响 |
| --- | --- | --- |
| 一、推广力度 | **A 轻量** | §8 只做 GitHub 站内、一篇中文社区帖、个人社群分享；知乎/视频/公众号/英文文档不做 |
| 二、身份公开度 | **A 只用 kkxh / DW YE** | 推广文案、issue 回复、commit 身份都不越线；不出现学校与真实姓名 |
| 三、公开后静置 | **A 静置 3–7 天** | §7 与 §8 之间留观察窗口：确认无隐私问题、CI 正常，再推广 |
| 四、提交者邮箱 | **A 换 kkxh + GitHub noreply** | 从下一个提交起生效；**不改写历史**，历史里原有的个人邮箱不追溯 |

### 0.4 绿灯命令（每提交前必跑）

```bash
python3 -m compileall -q .
python3 -m unittest discover -s tests -t .
TZ=UTC python3 -m unittest discover -s tests -t .
python3 seed_demo_data.py --check
bash scripts/privacy_scan.sh
bash scripts/privacy_scan.sh --all
```

### 0.5 评审点 R5（异步）

- **R5**：P4.0 完成后、**转公开之前** —— 评审「全历史扫描证据 + 依赖许可清单 + git 历史卫生 + LICENSE」，
  无阻塞项才允许执行转公开。评审只发本仓库内容（公开精简版代码与虚构数据）。
  **2026-09-26 已闭环：结论「无阻塞项，审计证据足以支撑转公开」。**

---

## 1. 目标与非目标

### 目标

把仓库从「能跑的私仓」变成「陌生人愿意点进来、敢 clone、能跑起来」的开源项目，
发布 `v0.1.0-alpha`，然后做**一轮轻量**推广（推广在转公开且静置之后）。

### 非目标（明确不做）

- 英文文档、多语言 README。
- 视频教程、公众号、知乎专栏。
- issue / PR 的响应时效承诺。
- 功能追赶上游（长期落后是预期状态）。
- 任何形式的真实数据、第三方题目、本机绝对路径进入仓库。

---

## 2. P4.0 发布前审计（硬性门槛）

### 2.1 全历史隐私扫描（发布前硬性待办）

`scripts/privacy_scan.sh` 原有三个模式（默认 / `--all` / `--strict`）看的都是**当前工作区或索引**，
扫不到「曾经提交过、后来删掉」的内容。2026-09-26 补上了第四个模式：

```bash
bash scripts/privacy_scan.sh --history   # 全部提交与历史文件版本 + 提交信息
```

- 实现：路径用 `git log --all --name-only --diff-filter=AM` 的全量文件名过一遍禁用路径规则；
  内容用 `git cat-file --batch-all-objects` 遍历**所有 blob 对象**（含已不在任何分支上的对象）；
  提交信息单独过一遍同一套模式。
- 为什么不在 CI 里跑：CI 是浅克隆（只有 1 个提交），跑这个模式会**看漏还装作通过**——
  正是 R3-4 那类「检查没跑成 ≠ 检查通过」的坑。所以它是**发布前的本地审计动作**，证据人工留档。
- 验收：输出形如「范围共 39 个提交 / 316 个历史文件版本，未发现真实数据、绝对路径或凭据痕迹」；
  并且有**反向用例**证明它真能抓到东西（内容、提交信息、禁用路径各一条，见 `tests/test_repo_hygiene.py`）。

### 2.2 git 历史卫生

- 提交信息：由 `--history` 的「提交信息」检查覆盖（本次实测 39 条提交信息干净）。
- 提交者身份：现有历史提交是 `Dio <yedw31@gmail.com>`——公开后会被永久索引。
  按决策四 A，从下一个提交起改用 `kkxh` + GitHub noreply 邮箱（`git config user.name/user.email`），
  **只改未来提交，不重写历史**；如果维护者知道自己的 GitHub 数字 ID，
  可换成 `<数字ID>+kkxh@users.noreply.github.com`，这样提交能挂到账号上。
- 检查命令（写进 R5 材料）：`git log --format='%an <%ae>' | sort -u`、`git log --format=%s%n%b`。

### 2.3 依赖许可清单

新增 `docs/THIRD_PARTY_LICENSES.md`：核对结果——运行时唯一第三方依赖是可选安装的
`openpyxl`（MIT，仅用于读 `.xlsx` 成绩表），其余全部是 Python 标准库；
开发依赖只有 `requirements-dev.txt`（它只是带上运行时依赖，测试用标准库 `unittest`）。
新依赖的准入规则沿用 AGENTS.md：许可证必须是 MIT / BSD / Apache-2.0，并说明为什么标准库不够用。

### 2.4 LICENSE 复核

`LICENSE` 是 MIT，署名 `Copyright (c) 2026 DW YE`（2026-09-21 填入）；发布前再人工看一眼，
确认没有 `<YOUR NAME>` 之类占位符（`privacy_scan.sh` 也会提醒）。

### 2.5 新机器可跑性（可复跑的「陌生人 clone」证据）

把现有 `test_repo_hygiene.ArchiveTests`（`git archive HEAD` 解包后跑最小闭环）扩展到 Phase 3 的命令：
解包后依次跑 `init-db --demo` → `import-scores --demo` → `import-questions --demo` →
`list-questions` → `recommend-questions` → `make-handout --recommend-for`，全部退出码 0。
这就是「换台机器 clone 下来能不能跑」的可复跑版本，不必只靠人工找人试。

### 改动文件

`scripts/privacy_scan.sh`（新增 `--history`）、`docs/PRIVACY.md`（补该模式）、
`docs/THIRD_PARTY_LICENSES.md`（新增）、`tests/test_repo_hygiene.py`（历史扫描的正反向用例）、
`docs/PHASE4_PLAN.md`（本文件）。

---

## 3. P4.1 陌生人 README（已完成 2026-09-26）

现在的 README 是维护者视角（「Phase N 完成了什么」）。第一次点进来的物理老师需要的是：

1. 一句话定位（本地优先、数据不出本机、面向中学物理教师）。
2. **30 秒 quickstart**：三条命令看到真实输出（`init-db --demo` → `import-scores --demo` → `make-report`），
   不装依赖、不写配置。
3. 截图：看板、周报、题目讲义各一张（见 §5）。
4. 功能清单 + **非目标声明**（不做多用户/不做在线题库/不追功能对等）。
5. FAQ（答案都是「不会」）：数据会上传吗？要联网吗？我的真实成绩和题库会被公开吗？
   我可以用自己的试卷吗？
6. 保留「上游私仓不在本仓库内、也不作为远端」的定位声明。

---

## 4. P4.2 架构说明（`docs/ARCHITECTURE.md`）（已完成 2026-09-26）

写清三件事：模块依赖（谁 import 谁、为什么没有循环）、数据流向
（配置 → 数据层 `db.py` → 各模块 → 报告/看板/API）、隐私边界（什么进仓库、什么永不进）。
模块依赖以实际 import 为准，不凭印象写。

---

## 5. P4.3 演示材料（`docs/screenshots/`）

- 内容：看板、周报、题目讲义各一张；数据全部来自 `seed_demo_data.py`（虚构）。
- 机制：看板是单文件 HTML，用无头浏览器（本机 playwright 可用）打开 `outputs/dashboard/index.html` 截图；
  周报与讲义是 Markdown，截图前先渲染。
- 入仓的是**裁好的 PNG**；原图留在 `outputs/`（已被 `.gitignore` 拒绝）。
- 图片是二进制，隐私扫描看不见内容，所以流程里加一条硬要求：
  **每张图入仓前人工逐张核对**（姓名是否「学生NN」、班名是否示例班、没有本机路径/窗口标题/通知气泡）。
- 可选：30 秒录屏放 Release 页（同样只用演示数据；放不放由维护者定）。

---

## 6. P4.4 发布文件（已完成 2026-09-26）

- `CHANGELOG.md`：`v0.1.0-alpha` 功能摘要（Phase 1–3 的能力）+ **已知限制**
  （单一学段示例配置、题库不含图片题与 PDF 导入、推荐是规则推荐、无多用户）。
- `CONTRIBUTING.md`：隐私红线（**不接受含真实数据、第三方题目、本机绝对路径的 PR**）、
  业余维护声明（不承诺响应时效）、功能不追赶上游、本地自检命令（四条绿灯 + `--history`）。
- `docs/RELEASE_CHECKLIST.md`：从审计到推广的勾选清单，把 §2/§7/§8 的每一步变成可打勾的条目。
- **版本口径统一**：README 顶部与徽章从 `v0.0.1` 改为 `v0.1.0-alpha`，与 CHANGELOG、tag 一致。

---

## 7. P4.5 发布执行（每一步都要维护者批准）

顺序固定，不可跳步：

1. 维护者批准后**转公开**（GitHub 仓库设置）；
2. 打 tag `v0.1.0-alpha`；
3. 创建 GitHub Release（正文取 CHANGELOG 的摘要 + 已知限制）。

转公开后的**收尾同步**（否则文档会自相矛盾）：

- `AGENTS.md` 第 3.7 条「本仓库当前是私有仓库……」→ 改成公开后的推送/发布政策；
- `docs/ROADMAP.md`「远端……当前为私有仓库」「发布节奏：Phase 4 完成后才把仓库转为公开」
  → 更新为已公开、版本号与发布节奏；
- `README.md` 的 clone 地址此时才是有效的（公开前陌生人 clone 会 404）。

**静置 3–7 天**（决策三 A）：观察 CI、隐私问题、陌生人的第一条反馈；无异常再进 §8。

---

## 8. P4.6 推广（发布后 + 静置后，轻量）

- **GitHub 站内**：仓库 description、topics（`physics` / `education` / `teaching-tool` / `chinese` / `sqlite`）、
  social preview 图（用 §5 的截图）。
- **中文技术社区一帖**（V2EX 或 LinuxDo，二选一即可）：叙事帖《一个高中物理老师的开源教学工具》——
  为什么写、本地优先的隐私设计、三张截图、30 秒 quickstart。
- **个人社群**：物理教师微信群/QQ 群分享（转化率最高的一档）。
- **暂不做**：知乎专栏、视频教程、英文文档——等第一波真实反馈再定。
- 红线：文案只用 `kkxh` / `DW YE` 身份，不出现学校名、真实姓名、班级、学生信息；
  发布动作由维护者执行，Codex 只出文案与清单。

---

## 9. P4.0 执行证据（2026-09-26）

执行人：Codex。相关提交：`32ed70e`（P4.0 主体）、`ad95143`（CI 暴露的跨平台问题修复）。

| 项目 | 命令 / 位置 | 结果 |
| --- | --- | --- |
| 全历史隐私扫描 | `bash scripts/privacy_scan.sh --history` | **通过**：40 个提交 / 321 个历史文件版本（含已不可达对象）与全部提交信息，未发现真实数据、绝对路径或凭据痕迹 |
| 工作区扫描三档 | `privacy_scan.sh` / `--all` / `--strict` | 全部通过（tracked 75 / worktree 75 / strict 76 个文件） |
| 历史扫描反向用例 | `tests/test_repo_hygiene.HistoryScanTests` | 3 条能抓到（历史内容、提交信息、历史禁用路径）+ 非 git 目录退出码 2 |
| 提交者身份 | `git log -1 --format='%an <%ae>'` | `kkxh <kkxh@users.noreply.github.com>`（决策四 A，自 P4.0 提交起生效） |
| 依赖许可清单 | `docs/THIRD_PARTY_LICENSES.md` | 运行时唯一第三方 `openpyxl`（MIT，可选安装）；其余全部标准库 |
| LICENSE | `LICENSE` 首行 | `MIT License` / `Copyright (c) 2026 DW YE`，无占位符 |
| 新机器可跑性 | `tests/test_repo_hygiene.ArchiveTests` | `git archive HEAD` 解包后跑通 quickstart + 题库命令（`list-questions` / `recommend-questions` / `make-handout` ×2），退出码全 0 |
| 全量回归 | `python3 -m unittest discover -s tests -t .` 与 `TZ=UTC …` | 各 **433 个用例全绿**（27 个 skip 是环境性的：沙箱不许绑回环端口、未装 `openpyxl`；CI 上都会实跑） |
| CI | GitHub Actions | `32ed70e` 首推暴露一个 bash 3.2/4-5 差异（内容检查静默跳过），修于 `ad95143`，CI 转绿（run 36219007903） |

**R5 待审材料**：本表 + `--history` 原始输出 + `docs/THIRD_PARTY_LICENSES.md` + 身份检查结果 + 新机器冒烟结果。

**已知限制（写进 R5 材料）**：历史里的旧提交仍署名 `Dio <yedw31@gmail.com>`——
按决策四 A 只改未来提交，不重写历史。

**R5 评审结论（2026-09-26，hira 静态评审）**：六项审计逐项通过，无阻塞项，证据足以支撑转公开；
转公开 → tag v0.1.0-alpha → Release 的执行由维护者决定。两个非阻塞观察：
① `--history` 的内容扫描里单个 blob 读取失败时静默跳过（worktree 路径有 grep 退出码检查，
 history 路径没有）；② 护栏用例只禁了 `${#arr[@]:-` 变体。回归测试 433 全绿采信执行者证据
（hira 未独立运行）。

---

## 待定清单

| 编号 | 事项 | 结论 |
| --- | --- | --- |
| 1 | 录屏放不放 Release | 维护者定；只允许用演示数据 |
| 2 | 英文文档 | 不做，等第一波反馈 |
| 3 | 是否把 `--history` 加进 CI | 暂不加（CI 是浅克隆，会看漏）；CI 若要跑，必须先 checkout `fetch-depth: 0` |
| 4 | GitHub 数字 ID | 维护者若提供，把 noreply 邮箱换成 `<数字ID>+kkxh@users.noreply.github.com` |
| 5 | 社区帖发哪个站 | V2EX / LinuxDo 二选一，发布前定 |

---

## 附录 A：发布前硬性待办核对表

| 待办（来自 ROADMAP） | 落点 | 证据 |
| --- | --- | --- |
| 补依赖许可清单 | §2.3 | `docs/THIRD_PARTY_LICENSES.md` |
| 对全部历史做一次隐私扫描 | §2.1 | `privacy_scan.sh --history` 输出 |
| 填写 LICENSE 署名 | §2.4 | `LICENSE` 首行 `Copyright (c) 2026 DW YE` |
| 依赖保持最小 | §2.3 | `requirements.txt` 仅 `openpyxl>=3.1` |
| 陌生人能跑通 | §2.5 | `git archive` 解包后的端到端用例 |

## 附录 B：30 秒 quickstart（README 与验收共用）

```bash
git clone https://github.com/kkxh/physics-teaching-hub.git
cd physics-teaching-hub
python3 hub.py init-db --demo
python3 hub.py import-scores --demo
python3 hub.py make-report
# 想试题库：python3 hub.py import-questions --demo && python3 hub.py list-questions
```

---

## 修订记录

2026-09-26（定稿；muse 初稿 → 本文件）：

1. **新增能力**：初稿说「对全部 git 历史跑 `--strict`」，但 `--strict` 扫的是工作区，
   历史根本扫不到。改为新增 `--history` 模式（路径 + 全部 blob + 提交信息），并配正反向用例；
   同时说明为什么它不进 CI（浅克隆会看漏）。
2. **补上提交者身份这一决策**：初稿只写「检查 `git config`」，但现有历史是
   `Dio <yedw31@gmail.com>`，公开后会被永久索引；升为决策四（A：换 kkxh + noreply，不改写历史）。
3. **补上截图的落地机制**：初稿写了要截图但没说怎么做；补无头浏览器方案、
   原图留 `outputs/`、PNG 需人工逐张核对（二进制扫不出来）。
4. **补上转公开后的文案同步**：`AGENTS.md` 3.7 与 `ROADMAP.md` 里的「当前为私有仓库」不更新会自相矛盾。
5. **把「陌生人能跑通」做实**：初稿靠人工找人试；补 `git archive` 解包后的端到端用例（可复跑）。
6. **版本口径统一**：README 的 `v0.0.1` 与 CHANGELOG 的 `v0.1.0-alpha` 对齐。
7. **核对初稿的三处事实**：依赖只有可选 `openpyxl`（MIT）、LICENSE 已署名 `DW YE`、
   CI 无 secrets 引用——全部属实，照写。
8. 记录四条决策（推广力度 A、身份公开度 A、静置 3–7 天 A、提交者邮箱 A）与 R5 评审点位置。
9. 2026-09-26：R5 评审闭环（结论「无阻塞项」）；P4.1 README、P4.2 架构说明、P4.4 发布文件落地
   （`CHANGELOG.md`、`CONTRIBUTING.md`、`docs/RELEASE_CHECKLIST.md`），README 版本口径升到 `v0.1.0-alpha`。
   余下 P4.3 截图与 A12 复核，然后才进入需要维护者批准的 P4.5。
