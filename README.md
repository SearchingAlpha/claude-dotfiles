# claude-dotfiles

Configuración portable para Claude Code: slash commands y skills que quiero tener
disponibles en cualquier máquina o entorno donde use Claude Code.

Fuente de verdad para `~/.claude/commands/*.md`. Los archivos viven aquí (en git)
y se replican al directorio de usuario de Claude Code mediante scripts de instalación.

## Estructura

```
claude-dotfiles/
├── commands/                    # slash commands (resolver, autobuild, builds, post, hs-*)
├── hackspain-starter/           # kit HackSpain 2026: scaffold Python + playbooks + cheat sheets
├── harnesses/
│   ├── autonomous-coding/       # harness de 3 agentes (se ejecuta in-place, no lo instala install.sh)
│   ├── eval-inspect/            # harness de evaluación formal de agentes con Inspect AI
│   └── tools/                   # utilidades compartidas (harness.py CLI, repomap.py, init_project.py multi-IDE)
├── skills/                      # skills generales
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

| Carpeta | ¿En el repo? | Contenido |
|---|---|---|
| `skills/` | Sí | Skills generales y de ingeniería (fuente: mattpocock/skills) |
| `skills-amazon/` | No (gitignored) | Skills con referencias a sistemas internos Amazon |

El repo es **privado** desde el 8 de septiembre de 2026 (contiene material personal
en `hackspain-starter/playbooks/`). `skills-amazon/` sigue fuera de git igualmente.

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
| `/repomap` | Extrae y carga el mapa semántico de símbolos del repo (estilo Aider Repo-Map) para orientar al modelo sin saturar contexto. |
| `/architect` | Modo Arquitecto (pensar primero, editar después): diseña la solución, mapea dependencias y diffs conceptuales sin tocar ficheros hasta aprobación. |
| `/eval` | Evaluador adversarial en sesión: adopta el rol de Evaluator escéptico independiente para intentar refutar que el cambio funciona mediante pruebas reales. |
| `/autobuild` | Lanza el harness autónomo de 3 agentes (Planner / Generator / Evaluator) sobre una spec. |
| `/builds` | Dashboard de solo lectura de las sesiones del harness. |
| `/post` | Bucle de contenido "Show Your Work" para LinkedIn/X sobre el vault de Obsidian. Un paso por invocación. |
| `/hs-frame` | HackSpain, hora 1: del texto del reto a un Framing Brief (one-liner, demo moment, tracer bullet, impacto, tracks). Sin código. |
| `/hs-demo` | HackSpain: guion de demo con golden path, pre-warm, escalera de fallback y reparto driver/narrador. |
| `/hs-pitch` | HackSpain: `draft` genera borrador EN + guion de entrega ES de 3 min con tiempos; `tighten` afina un guion pegado. |
| `/hs-judge` | HackSpain: Q&A simulado (`panel` rota personas VC/operador, `coffee` una pregunta abierta tipo FDE) puntuando el protocolo de 4 pasos. |
| `/hs-contact` | HackSpain: registra a una persona en `contacts.md` y redacta los follow-ups de 48h y 2 semanas. |
| `/hs-check` | HackSpain: `setup` copia las plantillas de coherencia (CONTRACT.md, AGENTS.md, check.sh, CI) al repo del proyecto; `review` pasa el check y revisa el diff contra el contrato. |

Los comandos `hs-*` leen su playbook en `hackspain-starter/playbooks/` antes de actuar; ver
`hackspain-starter/playbooks/README.md`. Están escritos en inglés.

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
