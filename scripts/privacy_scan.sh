#!/usr/bin/env bash
# 隐私扫描：提交前确认仓库里没有真实数据、凭据、绝对路径或私人标识。
#
# 用法：
#   bash scripts/privacy_scan.sh           # 扫描 Git 跟踪的文件（提交前的默认检查）
#   bash scripts/privacy_scan.sh --all     # 跟踪的 + 未跟踪但没被 .gitignore 拒绝的文件（= 可能被提交的文件）
#   bash scripts/privacy_scan.sh --strict  # 整个工作区，连 .gitignore 忽略的生成物一起扫（发布前审计用）
#
# 通用规则负责抓「结构性痕迹」（路径、密钥、身份证号、私有风格班号）。
# 学校名、真实人名这类词无法通用识别，请写进 .privacy-terms.local：
#   每行一个关键词，# 开头的行视为注释。
# 该文件已在 .gitignore 中，永不提交。
#
# 说明：跑完最小闭环（建库/导入/报告）后，工作区里会有 data/*.db、demo/、outputs/ 这些生成物；
# 默认与 --all 模式按 .gitignore 判断「会不会被提交」，所以照样通过；
# --strict 会把它们列出来提醒你——那是本机产物，不是要提交的内容。

set -uo pipefail

SELF_PATH="scripts/privacy_scan.sh"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

# 结构上不允许出现在公开仓库里的路径
FORBIDDEN_PATH_PATTERN='(^|/)(outputs|output|tmp|demo|data|教师用书)/|\.(db|sqlite|sqlite3|docx|doc|pptx|ppt|xlsx|xls|pdf)$|^config\.toml$|^config\.local\.toml$|^\.privacy-terms\.local$'
# 例外：目录说明文件本身可以入库
ALLOWED_PATH_PATTERN='^data/README\.md$'

patterns=(
  '/Users/[A-Za-z0-9._-]+'
  '/home/[A-Za-z0-9._-]+'
  'BEGIN [A-Z ]*PRIVATE KEY'
  'sk-[A-Za-z0-9]{16,}'
  'AKIA[0-9A-Z]{16}'
  'ghp_[A-Za-z0-9]{20,}'
  '高[0-9]{2}班'
  # 身份证号与手机号：两侧加边界，避免把浮点数的长数字串（如 0.9166666666666666）误判
  '(^|[^0-9Xx])[1-9][0-9]{16}[0-9Xx]([^0-9Xx]|$)'
  '(^|[^0-9])1[3-9][0-9]{9}([^0-9]|$)'
)

if [[ -f .privacy-terms.local ]]; then
  while IFS= read -r term; do
    [[ -z "$term" || "$term" == \#* ]] && continue
    patterns+=("$term")
  done < .privacy-terms.local
  echo "已加载本地私有词表 .privacy-terms.local"
fi

has_git=0
if git rev-parse --git-dir >/dev/null 2>&1; then
  has_git=1
fi

files=()
file_count=0
mode="tracked"

collect_from_git() {
  while IFS= read -r file; do
    files+=("$file")
    file_count=$((file_count + 1))
  done < <(git ls-files "$@")
}

collect_from_worktree() {
  while IFS= read -r file; do
    files+=("$file")
    file_count=$((file_count + 1))
  done < <(find . -type f \
    -not -path './.git/*' \
    -not -path './.venv/*' \
    -not -path '*/__pycache__/*' | sed 's|^\./||')
}

case "${1:-}" in
  --all)
    if [[ $has_git -eq 1 ]]; then
      mode="worktree(可能被提交的文件)"
      collect_from_git --cached --others --exclude-standard
    else
      mode="worktree(无 git，退化为整目录)"
      collect_from_worktree
    fi
    ;;
  --strict)
    mode="strict(整个工作区)"
    collect_from_worktree
    ;;
  *)
    if [[ $has_git -eq 1 ]] && [[ -n "$(git ls-files)" ]]; then
      collect_from_git
    else
      mode="worktree(无 git，退化为整目录)"
      collect_from_worktree
    fi
    ;;
esac

if [[ $file_count -eq 0 ]]; then
  echo "没有可扫描的文件。"
  exit 0
fi

status=0

for file in "${files[@]}"; do
  if [[ "$file" =~ $FORBIDDEN_PATH_PATTERN ]]; then
    if [[ "$file" =~ $ALLOWED_PATH_PATTERN ]]; then
      continue
    fi
    echo "[禁止入仓] $file"
    status=1
  fi
done

# 内容扫描只针对磁盘上确实存在的文件（--all 模式会列出已删除但仍在索引里的路径）
existing=()
for file in "${files[@]}"; do
  if [[ -f "$file" ]]; then
    existing+=("$file")
  fi
done

pattern_file="$(mktemp -t privacy_scan)"
trap 'rm -f "$pattern_file"' EXIT
printf '%s\n' "${patterns[@]}" > "$pattern_file"

if [[ ${#existing[@]} -gt 0 ]]; then
  if hits=$(grep -nIE -f "$pattern_file" --binary-files=without-match \
      --exclude="$SELF_PATH" -- "${existing[@]}" 2>/dev/null); then
    echo "发现疑似隐私或凭据内容："
    echo "$hits"
    status=1
  fi
fi

if grep -q "<YOUR NAME>" LICENSE 2>/dev/null; then
  echo "[提醒] LICENSE 中的版权署名还是占位符 <YOUR NAME>，公开发布前必须替换。"
fi

if [[ $status -eq 0 ]]; then
  echo "隐私扫描通过：$mode 范围共 $file_count 个文件，未发现真实数据、绝对路径或凭据痕迹。"
fi

exit $status
