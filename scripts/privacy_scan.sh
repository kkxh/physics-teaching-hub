#!/usr/bin/env bash
# 隐私扫描：提交前确认仓库里没有真实数据、凭据、绝对路径或私人标识。
#
# 用法：
#   bash scripts/privacy_scan.sh          # 扫描 Git 跟踪的文件（尚无提交时扫描工作区）
#   bash scripts/privacy_scan.sh --all    # 强制扫描整个工作区
#
# 通用规则负责抓「结构性痕迹」（路径、密钥、身份证号、私有风格班号）。
# 学校名、真实人名这类词无法通用识别，请写进 .privacy-terms.local：
#   每行一个关键词，# 开头的行视为注释。
# 该文件已在 .gitignore 中，永不提交。

set -uo pipefail

SELF_PATH="scripts/privacy_scan.sh"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

# 结构上不允许出现在公开仓库里的路径
FORBIDDEN_PATH_PATTERN='(^|/)(outputs|output|tmp|demo|教师用书)/|\.(db|sqlite|sqlite3|docx|doc|pptx|ppt|xlsx|xls|pdf)$|^config\.toml$|^config\.local\.toml$|^\.privacy-terms\.local$'

patterns=(
  '/Users/[A-Za-z0-9._-]+'
  '/home/[A-Za-z0-9._-]+'
  'BEGIN [A-Z ]*PRIVATE KEY'
  'sk-[A-Za-z0-9]{16,}'
  'AKIA[0-9A-Z]{16}'
  'ghp_[A-Za-z0-9]{20,}'
  '高[0-9]{2}班'
  '[1-9][0-9]{16}[0-9Xx]'
  '1[3-9][0-9]{9}'
)

if [[ -f .privacy-terms.local ]]; then
  while IFS= read -r term; do
    [[ -z "$term" || "$term" == \#* ]] && continue
    patterns+=("$term")
  done < .privacy-terms.local
  echo "已加载本地私有词表 .privacy-terms.local"
fi

files=()
file_count=0
mode="tracked"
if [[ "${1:-}" != "--all" ]] && git rev-parse --git-dir >/dev/null 2>&1 \
   && [[ -n "$(git ls-files)" ]]; then
  while IFS= read -r file; do
    files+=("$file")
    file_count=$((file_count + 1))
  done < <(git ls-files)
else
  mode="worktree"
  while IFS= read -r file; do
    files+=("$file")
    file_count=$((file_count + 1))
  done < <(find . -type f \
    -not -path './.git/*' \
    -not -path './demo/*' \
    -not -path './outputs/*' \
    -not -path './.venv/*' \
    -not -path '*/__pycache__/*' | sed 's|^\./||')
fi

if [[ $file_count -eq 0 ]]; then
  echo "没有可扫描的文件。"
  exit 0
fi

status=0

for file in "${files[@]}"; do
  if [[ "$file" =~ $FORBIDDEN_PATH_PATTERN ]]; then
    echo "[禁止入仓] $file"
    status=1
  fi
done

pattern_file="$(mktemp -t privacy_scan)"
trap 'rm -f "$pattern_file"' EXIT
printf '%s\n' "${patterns[@]}" > "$pattern_file"

if hits=$(grep -nIE -f "$pattern_file" --binary-files=without-match \
    --exclude="$SELF_PATH" -- "${files[@]}" 2>/dev/null); then
  echo "发现疑似隐私或凭据内容："
  echo "$hits"
  status=1
fi

if grep -q "<YOUR NAME>" LICENSE 2>/dev/null; then
  echo "[提醒] LICENSE 中的版权署名还是占位符 <YOUR NAME>，公开发布前必须替换。"
fi

if [[ $status -eq 0 ]]; then
  echo "隐私扫描通过：扫描 $mode 范围共 $file_count 个文件，未发现真实数据、绝对路径或凭据痕迹。"
fi

exit $status
