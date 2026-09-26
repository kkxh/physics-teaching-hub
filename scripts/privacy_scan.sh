#!/usr/bin/env bash
# 隐私扫描：提交前确认仓库里没有真实数据、凭据、绝对路径或私人标识。
#
# 用法：
#   bash scripts/privacy_scan.sh           # 扫描 Git 跟踪的文件（提交前的默认检查）
#   bash scripts/privacy_scan.sh --all     # 跟踪的 + 未跟踪但没被 .gitignore 拒绝的文件（= 可能被提交的文件）
#   bash scripts/privacy_scan.sh --strict  # 整个工作区，连 .gitignore 忽略的生成物一起扫（发布前审计用）
#   bash scripts/privacy_scan.sh --history # 全部提交与历史文件版本（发布前审计用；需要完整 git 历史）
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
history_mode=0

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

# ---- 历史扫描（--history）----
# 默认 / --all / --strict 都只看当前工作区或索引；「曾经提交过什么」必须单独扫。
# 思路：路径用 git log 的全量文件名，内容用仓库里所有 blob 对象，提交信息单独过一遍。
scan_history_paths() {
  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    if [[ "$path" =~ $FORBIDDEN_PATH_PATTERN ]] && ! [[ "$path" =~ $ALLOWED_PATH_PATTERN ]]; then
      echo "[禁止入仓] 历史提交里出现过：$path"
      status=1
    fi
  done < <(git log --all --pretty=format: --name-only --diff-filter=AM | sort -u)
}

scan_history_contents() {
  local object
  while IFS= read -r object; do
    [[ -z "$object" ]] && continue
    if hits=$(git cat-file -p "$object" 2>/dev/null \
        | grep -nIE -f "$pattern_file" --binary-files=without-match); then
      # 注意：变量后面紧挨中文标点时必须写成 ${object}，
      # 否则 bash 3.2 会把多字节字符当成变量名的一部分（object�: unbound variable）。
      echo "发现疑似隐私或凭据内容（历史文件版本 ${object}）："
      echo "$hits"
      status=1
    fi
  done < <(git cat-file --batch-all-objects --batch-check='%(objectname) %(objecttype)' \
             | awk '$2 == "blob" {print $1}')
}

scan_history_messages() {
  if hits=$(git log --all --format='%h %s%n%b' | grep -nIE -f "$pattern_file"); then
    echo "发现疑似隐私或凭据内容（提交信息）："
    echo "$hits"
    status=1
  fi
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
  --history)
    if [[ $has_git -ne 1 ]]; then
      echo "[错误] --history 需要 git 仓库：当前目录不是 git 工作区，历史扫描无法执行。"
      exit 2
    fi
    mode="history(全部提交与历史文件版本)"
    history_mode=1
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

if [[ $file_count -eq 0 && $history_mode -eq 0 ]]; then
  echo "没有可扫描的文件。"
  exit 0
fi

status=0

# bash 3.2 + set -u 下空数组的 "${arr[@]}" 会报 unbound variable，这里用 :- 兜底
for file in "${files[@]:-}"; do
  if [[ "$file" =~ $FORBIDDEN_PATH_PATTERN ]]; then
    if [[ "$file" =~ $ALLOWED_PATH_PATTERN ]]; then
      continue
    fi
    echo "[禁止入仓] $file"
    status=1
  fi
done

# 内容扫描只针对磁盘上确实存在的文件（--all 模式会列出已删除但仍在索引里的路径）。
# 这里用计数器而不是「数组长度」判断：macOS 自带 bash 3.2 在 set -u 下对空数组取长度会报
# unbound variable，而给长度加默认值的写法在 Linux 的 bash 4/5 上又是 bad substitution。
existing=()
existing_count=0
for file in "${files[@]:-}"; do
  if [[ -f "$file" ]]; then
    existing+=("$file")
    existing_count=$((existing_count + 1))
  fi
done

# 注意：`mktemp -t name` 在 GNU coreutils 上要求模板里带 XXX，在 macOS 上却能直接用，
# 所以这里显式给出模板——否则 Linux/CI 上会静默拿不到模式文件，内容检查变成空转。
pattern_file="$(mktemp "${TMPDIR:-/tmp}/privacy_scan.XXXXXX")" || true
if [[ -z "$pattern_file" || ! -f "$pattern_file" ]]; then
  echo "[错误] 无法创建临时模式文件，隐私扫描的内容检查没能执行；请检查 TMPDIR 权限。"
  exit 2
fi
trap 'rm -f "$pattern_file"' EXIT
printf '%s\n' "${patterns[@]}" > "$pattern_file"

if [[ $existing_count -gt 0 ]]; then
  if hits=$(grep -nIE -f "$pattern_file" --binary-files=without-match \
      --exclude="$SELF_PATH" -- "${existing[@]}"); then
    echo "发现疑似隐私或凭据内容："
    echo "$hits"
    status=1
  else
    grep_status=$?
    if (( grep_status > 1 )); then
      # grep 退出码 >1 说明扫描本身出错了（模式文件、参数、文件读取等），
      # 这种情况必须报错，不能让「检查没跑成」看起来像「检查通过」
      echo "[错误] 隐私扫描的内容检查执行失败（grep 退出码 ${grep_status}）。"
      status=1
    fi
  fi
fi

if [[ $history_mode -eq 1 ]]; then
  # 历史扫描需要完整历史（CI 的浅克隆只有 1 个提交，跑这个模式会看漏，
  # 所以它是发布前本地审计的硬性动作，不放进 CI）
  commit_total=$(git rev-list --all --count 2>/dev/null || echo 0)
  blob_total=$(git cat-file --batch-all-objects --batch-check='%(objecttype)' \
    | grep -c '^blob$' || true)
  scan_history_paths
  scan_history_contents
  scan_history_messages
fi

if grep -q "<YOUR NAME>" LICENSE 2>/dev/null; then
  echo "[提醒] LICENSE 中的版权署名还是占位符 <YOUR NAME>，公开发布前必须替换。"
fi

if [[ $status -eq 0 ]]; then
  if [[ $history_mode -eq 1 ]]; then
    echo "隐私扫描通过：$mode 范围共 $commit_total 个提交 / $blob_total 个历史文件版本，未发现真实数据、绝对路径或凭据痕迹。"
  else
    echo "隐私扫描通过：$mode 范围共 $file_count 个文件，未发现真实数据、绝对路径或凭据痕迹。"
  fi
fi

exit $status
