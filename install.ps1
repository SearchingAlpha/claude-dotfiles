# install.ps1 — sincroniza commands/*.md a $env:USERPROFILE\.claude\commands\
# Idempotente: vuelve a ejecutarlo tras un `git pull` para aplicar cambios.
# Estrategia: copia (no symlink) para evitar el requisito de permisos de admin
# o Developer Mode en Windows.
#
#   .\install.ps1                       copia los commands (/autobuild, /post...) + plugin
#   .\install.ps1 -PluginCommandsOnly   no copia commands y borra las copias y enlaces
#                                       del repo: solo quedan los del plugin,
#                                       /claude-dotfiles:<name>
#
# Los settings y los guards nunca se tocan: al final se imprime cómo aplicarlos.

[CmdletBinding()]
param(
    # Don't copy commands, and remove the repo's copies and links from
    # ~/.claude/commands. Trade-off: no short names (type /claude-dotfiles:<name>),
    # but each command is listed once. A later run without it copies them again.
    [switch]$PluginCommandsOnly
)

$ErrorActionPreference = 'Stop'

$repoRoot      = $PSScriptRoot
$sourceDir     = Join-Path $repoRoot 'commands'
$targetDir     = Join-Path $env:USERPROFILE '.claude\commands'

# Native git with its errors ignored: under 'Stop', Windows PowerShell 5.1
# turns redirected stderr into a terminating error, and git may be missing.
# Don't pass `--` through it: PowerShell drops `--` from a function's args.
function Invoke-Git {
    $ErrorActionPreference = 'Continue'
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { $global:LASTEXITCODE = 127; return }
    git @args 2>$null
}

# --- commands (default): copy commands/*.md into ~/.claude/commands ---
# Keeps the short names (/autobuild, /post, /hs-*) working; the plugin also
# lists each command as /claude-dotfiles:<name>.
function Copy-RepoCommands {
    if (-not (Test-Path $sourceDir)) {
        Write-Error "Source directory not found: $sourceDir"
        exit 1
    }

    if (-not (Test-Path $targetDir)) {
        Write-Host "Creating $targetDir"
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }

    $files = Get-ChildItem -Path $sourceDir -Filter '*.md' -File
    if ($files.Count -eq 0) {
        Write-Warning "No .md files found in $sourceDir"
        exit 0
    }

    foreach ($file in $files) {
        $dest = Join-Path $targetDir $file.Name
        Copy-Item -Path $file.FullName -Destination $dest -Force
        Write-Host "Installed: $($file.Name) -> $dest"
    }

    Write-Host ""
    Write-Host "Done. $($files.Count) command(s) installed to $targetDir"
}

# --- commands (-PluginCommandsOnly): remove what installs put in ~/.claude/commands ---
# Removed: symlinks into this repo's commands\ (this checkout, or the main
# checkout when run from a worktree; stale links too), and copies whose
# content is the repo file or any committed version of it (the installer
# copies, so a copy can predate later edits). A same-named file that matches
# none of them is left and listed. Nothing is copied.
function Remove-RepoCommands {
    if (-not (Test-Path -LiteralPath $targetDir)) { return }
    $commandDirs = @([System.IO.Path]::GetFullPath($sourceDir).TrimEnd('\'))
    $common = Invoke-Git -C $repoRoot rev-parse --git-common-dir
    if ($LASTEXITCODE -eq 0 -and $common) {
        if (-not [System.IO.Path]::IsPathRooted($common)) { $common = Join-Path $repoRoot $common }
        $common = [System.IO.Path]::GetFullPath($common).TrimEnd('\', '/')
        if ((Split-Path -Leaf $common) -eq '.git') {
            $commandDirs += (Join-Path (Split-Path -Parent $common) 'commands').TrimEnd('\')
        }
    }
    # "<name> <blob id>" for every version of commands/*.md in the history.
    $known = @{}
    $history = Invoke-Git -C $repoRoot log --format= --raw --no-abbrev --no-renames commands/
    if ($LASTEXITCODE -ne 0) { $history = @() }
    foreach ($line in $history) {
        if ($line -match '^:\S+ \S+ ([0-9a-f]{40,64}) ([0-9a-f]{40,64}) \S+\t(.+)$') {
            $name = Split-Path -Leaf $Matches[3]
            foreach ($id in @($Matches[1], $Matches[2])) {
                if ($id -notmatch '^0+$') { $known["$name $id"] = $true }
            }
        }
    }
    $removed = 0
    foreach ($entry in Get-ChildItem -LiteralPath $targetDir -Filter '*.md' -Force -ErrorAction SilentlyContinue) {
        if ($entry.PSIsContainer) { continue }
        $repoFile = Join-Path $sourceDir $entry.Name
        $isOurs = $false
        if ($entry.LinkType -eq 'SymbolicLink') {
            $target = [string]($entry.Target | Select-Object -First 1)
            if (-not [System.IO.Path]::IsPathRooted($target)) { $target = Join-Path $targetDir $target }
            $targetDirFull = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($target)).TrimEnd('\')
            if ($commandDirs -notcontains $targetDirFull) { continue }   # a link elsewhere: not ours
            $isOurs = $true
        } elseif (Test-Path -LiteralPath $repoFile) {
            $isOurs = ((Get-FileHash -LiteralPath $entry.FullName).Hash -eq (Get-FileHash -LiteralPath $repoFile).Hash)
            if (-not $isOurs -and $known.Count -gt 0) {
                # As git stores it (--path applies core.autocrlf), so a CRLF copy
                # of a committed LF version still matches.
                $blob = Invoke-Git -C $repoRoot hash-object --path "commands/$($entry.Name)" $entry.FullName
                if ($LASTEXITCODE -eq 0 -and $blob) {
                    $isOurs = $known.ContainsKey("$($entry.Name) $(([string]($blob | Select-Object -First 1)).Trim())")
                }
            }
        } else {
            continue                                           # not a repo command
        }
        if ($isOurs) {
            Remove-Item -LiteralPath $entry.FullName -Force
            Write-Host "Removed command: $($entry.Name)"
            $removed++
        } else {
            Write-Warning "Left $($entry.FullName): matches no version of the repo file (the plugin provides /claude-dotfiles:$($entry.BaseName))"
        }
    }
    if ($removed -gt 0) {
        Write-Host "Done. $removed command file(s) removed from $targetDir."
        Write-Host "Short names are gone: type /claude-dotfiles:<name> (e.g. /claude-dotfiles:autobuild). Run install.ps1 without -PluginCommandsOnly to copy them again."
    } else {
        Write-Host "No copies or links of this repo's commands in $targetDir."
    }
}

if ($PluginCommandsOnly) {
    Write-Host "-PluginCommandsOnly: commands are not copied; the plugin provides them as /claude-dotfiles:<name>."
    Remove-RepoCommands
} else {
    Copy-RepoCommands
}

# --- skills (plugin local: el repo es marketplace + plugin) ---
Write-Host ""
Write-Host "Installing skills plugin..."
$ErrorActionPreference = 'Continue'
claude plugin marketplace add $repoRoot 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { claude plugin marketplace update claude-dotfiles 2>$null | Out-Null }
claude plugin install claude-dotfiles@claude-dotfiles --scope user
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Plugin install failed. Run manually: claude plugin marketplace add $repoRoot; claude plugin install claude-dotfiles@claude-dotfiles --scope user"
} else {
    Write-Host "Plugin installed."
}

# --- settings and guards (printed, never run) ---
$merge = Join-Path $repoRoot 'settings\merge.py'
$userTpl = Join-Path $repoRoot 'settings\user.template.json'
$gatesTpl = Join-Path $repoRoot 'settings\gates.template.json'
Write-Host ""
Write-Host "Settings are not changed by this script. Preview the merge into ~/.claude/settings.json:"
Write-Host "  python `"$merge`" `"$userTpl`" `"$gatesTpl`""
Write-Host "Add --out <file> for a 'claude --settings <file>' trial, or --apply to write it (with a backup)."
Write-Host "The status line scripts are bash: on Windows they need Git Bash. See $(Join-Path $repoRoot 'settings\README.md')."
# Git for Windows checks out with CRLF by default (core.autocrlf=true), and
# bash can't run a script with CRLF line endings: the status line goes blank.
$slScript = Join-Path $repoRoot 'statusline\statusline.sh'
if ((Test-Path -LiteralPath $slScript) -and ([System.IO.File]::ReadAllText($slScript) -match "`r`n")) {
    Write-Warning "statusline\*.sh have CRLF line endings, which bash can't run. See 'Windows' in $(Join-Path $repoRoot 'statusline\README.md')."
}
$scan = Join-Path $repoRoot 'hooks\guards\precommit_scan.py'
$apply = (Join-Path $repoRoot 'hooks\guards\amazon\apply.sh') -replace '\\', '/'
Write-Host ""
Write-Host "Guards are not installed by this script either. Run these from the main checkout (they record its paths):"
Write-Host "  python `"$scan`" install <repo>   # git pre-commit secret scan for <repo>"
Write-Host "  python `"$scan`" status <repo>    # what that repo's hook runs, and which python it found"
Write-Host "  sh `"$apply`"                     # in Git Bash; dry run: the Amazon guards' settings diff, nothing written"
Write-Host "See $(Join-Path $repoRoot 'hooks\guards\README.md') and $(Join-Path $repoRoot 'hooks\guards\amazon\README.md')."
if (-not $PluginCommandsOnly) {
    Write-Host ""
    Write-Host "Each command is listed twice, as /<name> and /claude-dotfiles:<name>. To keep only the plugin's: .\install.ps1 -PluginCommandsOnly (no short names)."
}

Write-Host ""
Write-Host "Restart Claude Code (or run /help) to see all commands and skills."
