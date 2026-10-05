#!/usr/bin/env bash
# install.sh — sincroniza commands/*.md a ~/.claude/commands/ y registra skills como plugin local
# Idempotente: re-ejecuta tras `git pull` para aplicar cambios.
# Estrategia: symlinks (Linux/Mac) para que los cambios en el repo se vean
# inmediatamente sin reinstalar.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$repo_root/commands"
target_dir="$HOME/.claude/commands"

# --- commands ---
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
else
    for file in "${files[@]}"; do
        name="$(basename "$file")"
        dest="$target_dir/$name"
        ln -sfn "$file" "$dest"
        echo "Linked: $name -> $dest"
    done
    echo
    echo "Done. ${#files[@]} command(s) linked to $target_dir"
fi

# --- shepherd CLI ---
mkdir -p "$HOME/.local/bin"
ln -sfn "$repo_root/harnesses/shepherd/shepherd.py" "$HOME/.local/bin/shepherd"
echo
echo "Linked: shepherd -> $HOME/.local/bin/shepherd"

# --- private skills (gitignored skills-amazon/: never in plugin.json) ---
# Each subfolder with a SKILL.md becomes a user skill; its install.sh, if any,
# runs too (e.g. to drop a shepherd review backend into ~/.claude/shepherd/backends/).
shopt -s nullglob
for dir in "$repo_root"/skills-amazon/*/; do
    dir="${dir%/}"
    [[ -f "$dir/SKILL.md" ]] || continue
    mkdir -p "$HOME/.claude/skills"
    ln -sfn "$dir" "$HOME/.claude/skills/$(basename "$dir")"
    echo "Linked private skill: $(basename "$dir")"
    [[ -x "$dir/install.sh" ]] && "$dir/install.sh"
done
shopt -u nullglob

# --- skills (install as local plugin) ---
echo
echo "Installing skills plugin..."
# El repo es a la vez marketplace local (.claude-plugin/marketplace.json) y plugin.
claude plugin marketplace add "$repo_root" >/dev/null 2>&1 || claude plugin marketplace update claude-dotfiles >/dev/null 2>&1 || true
claude plugin install claude-dotfiles@claude-dotfiles --scope user 2>&1 && echo "Plugin installed." || echo "Plugin install failed — run manually: claude plugin marketplace add $repo_root && claude plugin install claude-dotfiles@claude-dotfiles --scope user"

echo
echo "Restart Claude Code (or run /help) to see all commands and skills."
