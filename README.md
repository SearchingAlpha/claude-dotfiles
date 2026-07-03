# claude-dotfiles

Configuración portable para Claude Code: slash commands y skills que quiero tener
disponibles en cualquier máquina o entorno donde use Claude Code.

Fuente de verdad para `~/.claude/commands/*.md`. Los archivos viven aquí (en git)
y se replican al directorio de usuario de Claude Code mediante scripts de instalación.

## Estructura

```
claude-dotfiles/
├── commands/                    # slash commands (resolver.md, ...)
├── skills/                      # skills generales — se commitean a GitHub
│   ├── grilling/
│   ├── grill-me/
│   ├── writing-great-skills/
│   ├── diagnosing-bugs/
│   ├── tdd/
│   ├── prototype/
│   ├── domain-modeling/
│   ├── handoff/
│   ├── to-issues/
│   └── code-review/
├── skills-amazon/               # skills internas Amazon — GITIGNORED, nunca commitear
│   └── README.md
├── .claude-plugin/plugin.json   # manifiesto del plugin de skills
├── .gitignore                   # excluye skills-amazon/
├── install.ps1                  # instala en Windows
├── install.sh                   # instala en Linux/Mac (commands + plugin de skills)
└── README.md
```

### skills/ vs skills-amazon/

| Carpeta | ¿En GitHub? | Contenido |
|---|---|---|
| `skills/` | Sí | Skills generales y de ingeniería (fuente: mattpocock/skills) |
| `skills-amazon/` | No (gitignored) | Skills con referencias a sistemas internos Amazon |

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

## Skills disponibles

Las skills se instalan como plugin via `claude plugin install`. `install.sh` lo hace automáticamente.

### Productividad

| Skill | Tipo | Descripción |
|---|---|---|
| `grilling` | model-invoked | Entrevista relentless sobre cada aspecto de un plan hasta resolver todas las decisiones. |
| `grill-me` | user-invoked | Lanza una sesión `/grilling`. |
| `handoff` | user-invoked | Compacta la conversación en un doc de continuidad para la próxima sesión. |
| `writing-great-skills` | user-invoked | Referencia para escribir y editar skills — vocabulario y principios de predictabilidad. |
| `roast-my-post` | user-invoked | Gallo/roaster de borradores (LinkedIn/social, ES o EN): puntúa cuánto suena a IA, destroza los tells citando tu texto, y lo reescribe con voz humana. |

### Ingeniería

| Skill | Tipo | Descripción |
|---|---|---|
| `diagnosing-bugs` | model-invoked | Loop estructurado de 6 fases para bugs difíciles: feedback loop → repro → hipótesis → instrumentar → fix → post-mortem. |
| `tdd` | model-invoked | Red-green-refactor loop. Referencia de qué es un buen test, seams, anti-patterns y reglas del loop. |
| `prototype` | model-invoked | Prototipo throwaway para responder una pregunta de diseño (rama lógica o rama UI). |
| `domain-modeling` | model-invoked | Construye y afina el modelo de dominio: desafía términos, captura CONTEXT.md y ADRs. |
| `to-issues` | user-invoked | Rompe un plan/PRD en issues independientes usando vertical slices (tracer bullets). |
| `code-review` | model-invoked | Review en dos ejes paralelos: Standards (smells + convenciones) y Spec (¿implementa lo pedido?). |

Fuente: [mattpocock/skills](https://github.com/mattpocock/skills)

## Móvil / claude.ai web

Los slash commands de filesystem no aplican en claude.ai web/móvil.
Para llevar un comando a esos entornos: copiar el contenido del `.md`
(sin el frontmatter) como system prompt de un Project en claude.ai.
