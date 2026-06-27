# claude-dotfiles

Configuración portable para Claude Code: slash commands y skills que quiero tener
disponibles en cualquier máquina o entorno donde use Claude Code.

Fuente de verdad para `~/.claude/commands/*.md`. Los archivos viven aquí (en git)
y se replican al directorio de usuario de Claude Code mediante scripts de instalación.

## Estructura

```
claude-dotfiles/
├── commands/        # slash commands (resolver.md, ...)
├── install.ps1      # instala en Windows (copia a %USERPROFILE%\.claude\commands\)
├── install.sh       # instala en Linux/Mac (symlink a ~/.claude/commands/)
└── README.md
```

## Instalación

### Windows (PowerShell)

```powershell
cd <ruta-del-repo>
./install.ps1
```

### Linux / macOS

```bash
cd <ruta-del-repo>
./install.sh
```

Ambos scripts son idempotentes: vuelves a ejecutarlos cuando hagas `git pull`
para sincronizar cambios.

## Uso desde otro entorno

```bash
git clone <url-del-repo> ~/claude-dotfiles
cd ~/claude-dotfiles
./install.sh   # o ./install.ps1 en Windows
```

## Slash commands disponibles

| Comando | Descripción |
|---|---|
| `/resolver` | Protocolo de resolución de problemas con IA: clasifica el tipo, ejecuta los 7 pasos con mapeo humano↔LLM, e incluye verificación adversarial contra sycophancy. |

## Móvil / claude.ai web

Los slash commands de filesystem no aplican en claude.ai web/móvil.
Para llevar un comando a esos entornos: copiar el contenido del `.md`
(sin el frontmatter) como system prompt de un Project en claude.ai.
