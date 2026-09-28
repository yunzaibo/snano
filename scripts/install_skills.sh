#!/usr/bin/env bash
set -euo pipefail

PACKAGE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_ROOT="${SNANO_SKILLS_HOME:-$HOME/.codex/skills}"
mkdir -p "$TARGET_ROOT"

# Check both sources before changing an existing installation.
for skill in snano simage; do
  test -f "$PACKAGE_ROOT/skills/$skill/SKILL.md"
done

for skill in snano simage; do
  source_dir="$PACKAGE_ROOT/skills/$skill"
  target_dir="$TARGET_ROOT/$skill"
  if [[ ! -d "$source_dir" ]]; then
    echo "缺少 skill: $source_dir" >&2
    exit 1
  fi
  if [[ -e "$target_dir" || -L "$target_dir" ]]; then
    backup_root="$(mktemp -d "${TARGET_ROOT%/}.snano-backup.XXXXXX")"
    backup="$backup_root/$skill"
    mv "$target_dir" "$backup"
    echo "已备份 $skill -> $backup"
  fi
  ln -s "$source_dir" "$target_dir"
  echo "已安装 $skill -> $target_dir"
done

echo "安装完成：只注册 snano 和 simage 两个 skill。"
