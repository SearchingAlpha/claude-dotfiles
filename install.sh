#!/usr/bin/env bash
# install.sh — sincroniza commands/*.md a ~/.claude/commands/
# Idempotente: re-ejecuta tras `git pull` para aplicar cambios.
# Estrategia: symlinks (Linux/Mac) para que los cambios en el repo se vean
# inmediatamente sin reinstalar.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$repo_root/commands"
target_dir="$HOME/.claude/commands"

if [[ ! -d "$source_dir" ]]; then
    echo "Source directory not found: $source_dir" >&2
    exit 1
fi

mkdir -p "$target_dir"

shopt -s nullglob
files=("$source_dir"/*.md)
shopt -u nullglob

if [[ ${#files[@]} -eq 0 ]]; then
    echo "No .md files found in $source_dir"
    exit 0
fi

for file in "${files[@]}"; do
    name="$(basename "$file")"
    dest="$target_dir/$name"
    ln -sfn "$file" "$dest"
    echo "Linked: $name -> $dest"
done

echo
echo "Done. ${#files[@]} command(s) linked to $target_dir"
echo "Restart Claude Code (or run /help) to see them."
