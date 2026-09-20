# install.ps1 — sincroniza commands/*.md a $env:USERPROFILE\.claude\commands\
# Idempotente: vuelve a ejecutarlo tras un `git pull` para aplicar cambios.
# Estrategia: copia (no symlink) para evitar el requisito de permisos de admin
# o Developer Mode en Windows.

$ErrorActionPreference = 'Stop'

$repoRoot      = $PSScriptRoot
$sourceDir     = Join-Path $repoRoot 'commands'
$targetDir     = Join-Path $env:USERPROFILE '.claude\commands'

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

Write-Host ""
Write-Host "Restart Claude Code (or run /help) to see all commands and skills."
