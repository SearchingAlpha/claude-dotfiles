# seo-project.json — esquema, descubrimiento e invocación

Un fichero por proyecto consumidor. Las skills no contienen nada del proyecto: todo sale de aquí.
Esquema completo y plantilla: `harnesses/tools/seo/README.md` (§ seo-project.json). Ejemplo real:
`~/workplace/agentic-marketplace/site/config/seo-project.json`.

## Descubrimiento (en este orden, parar en el primero)

1. `--project <ruta>` en `$ARGUMENTS`.
2. Desde el cwd hacia arriba, en cada nivel: `seo-project.json`, `site/config/seo-project.json`,
   `config/seo-project.json`, `.claude/seo-project.json`.
3. `$SEO_PROJECT`.
4. Nada → imprime `Sin seo-project.json. Ejecuta /seo-setup.` y **para**. No inventes un config.

## Invocación de la librería

```
S="<python_bin> <scripts.dir>/seo.py --project <cfg>"     # python_bin y scripts.dir del config
$S <cmd> … --json          # resultado JSON en stdout; diagnóstico en stderr
```

- `scripts.dir` lleva `~`: expándelo (`$HOME`). Dentro de comillas el shell **no** expande `~`.
- Exit 0 ok · 1 config/secreto · 2 todo falló · 3 cuota rechazada. Ante 1-3: cita el stderr
  literal en el informe y sigue con las fuentes que queden. **No rellenes** lo que faltó.
- `--dry-run` imprime llamadas y coste sin red: es el **cost gate** (ver REPORT-FORMAT).
- `scripts.gsc_queries` (opcional, script del proyecto) se ejecuta directo:
  `<python_bin> <scripts.gsc_queries> --days N [--contains S] --json`. Si es `null`, GSC = n/d.

## Claves que las skills leen

| Clave | Uso |
|---|---|
| `domain`, `site_url`, `brand.{name,tokens}` | identidad; `is_ours`, navigational |
| `positioning` | frase honesta de qué es el negocio; va en cada ángulo de outreach |
| `geo.metros[]` `{id, name, county, serpapi_location, cities[]}` | argumento `<metro>` de las skills = `id` o `name` |
| `queries.{money,brand,tracking}` | set de seguimiento; `rank-track add` escribe en `tracking` |
| `competitors[]` `{domain, type, metro, note}` + `domain_classes` | roster; `type` ∈ operator-local, brand-collision |
| `seeds`, `page_templates`, `linkable_assets` | keywords: plantillas, página destino; links: activos |
| `budgets`, `serpapi_reserve`, `per_run` | cuota; `per_run` = umbral que dispara el cost gate |
| `sources.*` | JSONL que ya produce el proyecto (serp_watch, gsc…); **leer, no relanzar** |
| `context_docs[]` | docs de estrategia a leer para contexto (offpage, auditorías) |
| `ops_dir` | salidas: JSONL de la librería, informes `<tipo>-<sujeto>-<fecha>.md`, `research-log.jsonl` |
| `sources_verified` | `{fuente: "YYYY-MM-DD" | "missing: <ruta>"}`; lo escribe /seo-setup |

Rutas relativas se resuelven contra el directorio del JSON (la librería ya lo hace; si lees
un fichero del config a mano, resuélvela igual).
