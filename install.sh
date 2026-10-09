#!/usr/bin/env bash
# install.sh — sincroniza commands/*.md a ~/.claude/commands/ y registra skills como plugin local
# Idempotente: re-ejecuta tras `git pull` para aplicar cambios.
# Estrategia: symlinks (Linux/Mac) para que los cambios en el repo se vean
# inmediatamente sin reinstalar.
#
#   install.sh                          enlaza los commands (/autobuild, /post…) + plugin
#   install.sh --plugin-commands-only   no enlaza commands y borra los enlaces al repo:
#                                       solo quedan los del plugin, /claude-dotfiles:<name>
#
# Los settings y los guards nunca se tocan: al final se imprime cómo aplicarlos.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: install.sh [--plugin-commands-only]

Links commands/*.md into ~/.claude/commands (short names: /autobuild, /post, ...),
links the shepherd CLI and private skills, and installs the repo as a local plugin.
The plugin lists the same commands again as /claude-dotfiles:<name>.

  --plugin-commands-only  Don't link commands, and remove the links into this
                          repo's commands/ that earlier runs created. Trade-off:
                          no short names (type /claude-dotfiles:<name>), but each
                          command is listed once. A later run without the flag
                          links them again.
  -h, --help              Show this help.

Settings and guards are never applied: the commands to preview them are printed.
EOF
}

plugin_commands_only=0
for arg in "$@"; do
    case "$arg" in
        --plugin-commands-only) plugin_commands_only=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "install.sh: unknown option: $arg" >&2; usage >&2; exit 2 ;;
    esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$repo_root/commands"
target_dir="$HOME/.claude/commands"

# --- commands (default): link commands/*.md into ~/.claude/commands ---
# Keeps the short names (/autobuild, /post, /hs-*) working; the plugin also
# lists each command as /claude-dotfiles:<name>.
link_repo_commands() {
    local source_dir="$1" target_dir="$2" file name dest
    local -a files
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
}

# --- commands (--plugin-commands-only): remove the links into this repo ---
# Only symlinks whose target is this repo's commands/ (this checkout, or the
# main checkout when run from a worktree; stale links too) are removed;
# regular files and links elsewhere are left alone. Nothing is linked.
unlink_repo_commands() {
    local dir="$1" link target tdir root removed=0
    shift
    [[ -d "$dir" ]] || return 0
    for link in "$dir"/*.md; do
        [[ -L "$link" ]] || continue
        target="$(readlink "$link")"
        [[ "$target" == /* ]] || target="$dir/$target"
        tdir="$(cd "$(dirname "$target")" 2>/dev/null && pwd -P)" || tdir="$(dirname "$target")"
        for root in "$@"; do
            [[ -n "$root" ]] || continue
            if [[ "$tdir" == "$root/commands" ]]; then
                rm -f "$link"
                echo "Removed command link: $(basename "$link")"
                removed=$((removed + 1))
                break
            fi
        done
    done
    if [[ $removed -gt 0 ]]; then
        echo "Done. $removed link(s) removed from $dir."
        echo "Short names are gone: type /claude-dotfiles:<name> (e.g. /claude-dotfiles:autobuild)." \
             "Run install.sh without --plugin-commands-only to link them again."
    else
        echo "No links into this repo's commands/ in $dir."
    fi
}

if [[ $plugin_commands_only -eq 1 ]]; then
    repo_phys="$(cd "$repo_root" && pwd -P)"
    main_root=""
    if common="$(git -C "$repo_root" rev-parse --git-common-dir 2>/dev/null)"; then
        [[ "$common" == /* ]] || common="$repo_root/$common"
        [[ "$(basename "$common")" == ".git" ]] && main_root="$(cd "$common/.." 2>/dev/null && pwd -P)" || true
    fi
    echo "--plugin-commands-only: commands are not linked; the plugin provides them as /claude-dotfiles:<name>."
    unlink_repo_commands "$target_dir" "$repo_root" "$repo_phys" "$main_root"
else
    link_repo_commands "$source_dir" "$target_dir"
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

# --- settings and guards (printed, never run) ---
echo
echo "Settings are not changed by this script. Preview the merge into ~/.claude/settings.json:"
echo "  python3 \"$repo_root/settings/merge.py\" \"$repo_root/settings/user.template.json\" \"$repo_root/settings/gates.template.json\""
echo "Add --out <file> for a 'claude --settings <file>' trial, or --apply to write it (with a backup)."
echo "See $repo_root/settings/README.md."
echo
echo "Guards are not installed by this script either. Run these from the main checkout (they record its paths):"
echo "  python3 \"$repo_root/hooks/guards/precommit_scan.py\" install <repo>   # git pre-commit secret scan for <repo>"
echo "  python3 \"$repo_root/hooks/guards/precommit_scan.py\" status <repo>    # what that repo's hook runs"
echo "  sh \"$repo_root/hooks/guards/amazon/apply.sh\"                          # dry run: the Amazon guards' settings diff, nothing written"
echo "See $repo_root/hooks/guards/README.md and $repo_root/hooks/guards/amazon/README.md."
if [[ $plugin_commands_only -eq 0 ]]; then
    echo
    echo "Each command is listed twice, as /<name> and /claude-dotfiles:<name>." \
         "To keep only the plugin's: install.sh --plugin-commands-only (no short names)."
fi

echo
echo "Restart Claude Code (or run /help) to see all commands and skills."
