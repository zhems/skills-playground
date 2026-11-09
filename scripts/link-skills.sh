#!/usr/bin/env bash
# Symlink each skill in this repo's .claude/skills/ into ~/.claude/skills/.
# Idempotent. Never overwrites anything it didn't create; never touches `synced`.
set -u

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo_root/.claude/skills"
dest="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"

mkdir -p "$dest"

# Link every skill directory (one containing SKILL.md).
for dir in "$src"/*/; do
  [ -f "${dir}SKILL.md" ] || continue
  name="$(basename "$dir")"
  target="$src/$name"
  link="$dest/$name"

  if [ -L "$link" ]; then
    [ "$(readlink "$link")" = "$target" ] && continue
    echo "link-skills: skipped $name (existing link points elsewhere: $(readlink "$link"))" >&2
  elif [ -e "$link" ]; then
    echo "link-skills: skipped $name (a real file/directory already exists at $link)" >&2
  else
    ln -s "$target" "$link" && echo "link-skills: linked $name"
  fi
done

# Remove dangling links that point into this repo (skill deleted or renamed).
for link in "$dest"/*; do
  [ -L "$link" ] || continue
  [ -e "$link" ] && continue
  case "$(readlink "$link")" in
    "$src"/*) rm "$link" && echo "link-skills: removed stale link $(basename "$link")" ;;
  esac
done

exit 0
