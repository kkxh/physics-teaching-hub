# 发布检查清单

从「仓库私有、代码可用」走到「公开发布 v0.1.0-alpha」的勾选清单。
**每一步都要维护者明确批准**；转公开、打 tag、发 Release 这三步尤其不能跳。

## A. 发布前准备（不需要批准，做完就好）

- [x] A1 全历史隐私扫描：`bash scripts/privacy_scan.sh --history` 通过，输出留档
      （见 [PHASE4_PLAN.md](PHASE4_PLAN.md) §9；2026-09-26：40 个提交 / 321 个历史文件版本，干净）
- [x] A2 工作区三档扫描：`privacy_scan.sh`、`--all`、`--strict` 全部通过
- [x] A3 依赖许可清单：[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)
      （唯一第三方 `openpyxl`，MIT，可选安装）
- [x] A4 LICENSE 复核：MIT，`Copyright (c) 2026 DW YE`，无占位符
- [x] A5 提交者身份：从 P4.0 起为 `kkxh <kkxh@users.noreply.github.com>`（历史不重写）
- [x] A6 新机器可跑通：`tests/test_repo_hygiene.ArchiveTests`
      （`git archive` 解包后跑 quickstart + 题库命令，退出码全 0）
- [x] A7 陌生人 README（含 30 秒 quickstart、隐私 FAQ、非目标）
- [x] A8 架构说明：[ARCHITECTURE.md](ARCHITECTURE.md)
- [x] A9 发布文件：`CHANGELOG.md`、`CONTRIBUTING.md`、本文件
- [x] A10 截图入仓：`docs/screenshots/`（`dashboard.png` / `weekly-report.png` /
      `question-handout.png`，全部来自演示数据；逐张人工核对：姓名均为「学生NN」、
      班级为示例班名、无本机路径与窗口痕迹，见 PHASE4_PLAN §5）
- [x] A11 版本口径统一：README 顶部与 CHANGELOG 都写 `v0.1.0-alpha`
- [x] A12 终检（2026-09-26，HEAD `a986b7d`，截图入仓后重跑）：`compileall` OK；
      433 个用例在 Asia/Shanghai 与 `TZ=UTC` 下各全绿（27 skip 为环境性）；
      `seed_demo_data.py --check` 通过；隐私扫描四档全过
      （tracked 82 / worktree 82 / strict 83 个文件；history 44 个提交 / 337 个历史文件版本）

## B. R5 评审（异步，转公开之前）

- [x] B1 R5 结论：**无阻塞项**，审计证据足以支撑转公开（2026-09-26）
- [x] B2 R5 之后的改动（P4.1/P4.2/P4.4 文档、P4.3 截图、A12 记录）已重跑 `--history`
      与全量检查，证据补进 PHASE4_PLAN §9（44 个提交 / 337 个历史文件版本，干净）

## C. 需要维护者批准的动作（**逐步确认，不要连着做**）

- [x] C1 **转公开**（2026-09-27，维护者操作）——核对结果：`visibility=public`、
      `default_branch=main`、只有 `main` 一个分支、无 tag 与 Release、
      LICENSE 识别为 MIT、本地 HEAD 与 `origin/main` 一致（`e3272f9`）
- [x] C2 转公开后同步文案（2026-09-27 完成）：
      `AGENTS.md` 第 3.7 条改为「已于 2026-09-27 转为公开；打 tag / 发 Release / 转回私有 /
      重写历史需批准」；`docs/ROADMAP.md` 的远端、推送政策、发布节奏改为已公开，
      两条发布前硬性待办标记为已完成；README 的 clone 地址公开后已可直接使用。
- [ ] C3 打 tag `v0.1.0-alpha`（维护者操作）
- [ ] C4 创建 GitHub Release，正文取 `CHANGELOG.md` 的摘要 + 已知限制（维护者操作）
- [ ] C5 Release 页可选：30 秒录屏（只允许演示数据；放不放由维护者定）

## D. 静置期（决策三 A：3–7 天）

- [ ] D1 观察公开后的 CI 是否正常
- [ ] D2 确认没有隐私反馈（Issue / 私信 / 自己再看一遍仓库首页）
- [ ] D3 静置结束、无异常，才进入推广

## E. 推广（发布后 + 静置后；轻量）

- [ ] E1 GitHub 站内：description、topics（`physics` / `education` / `teaching-tool` / `chinese` / `sqlite`）、
      social preview 图（用 A10 的截图）
- [ ] E2 中文技术社区一帖（V2EX **或** LinuxDo，二选一）：叙事帖《一个高中物理老师的开源教学工具》，
      正文只出现 `kkxh` / `DW YE` 身份，不出现学校名与真实姓名
- [ ] E3 个人社群（物理教师微信群/QQ 群）分享
- [ ] E4 暂不做：知乎专栏、视频教程、英文文档——等第一波反馈再定

## F. 出问题时的回滚

- 发现隐私内容：按 [PRIVACY.md](PRIVACY.md) 第 6 节处理（删除内容 + 清理历史），必要时先转回私有；
- 发现 CI 红：先修，再考虑是否影响已发布的 tag（已发布的 tag 不移动）。
