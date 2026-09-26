---
name: seo-setup
description: "Crea o actualiza el seo-project.json de un repo: infiere del código lo que pueda, pregunta solo lo no inferible, prueba cada fuente (probe) y deja sources_verified. Invócala con /seo-setup [ruta]."
disable-model-invocation: true
argument-hint: "[ruta destino]"
license: "MIT (metodología derivada de open-seo, ver ../seo/NOTICE.md)"
---

# seo-setup

Absorbe `seo-project-setup` de open-seo. Deja un `seo-project.json` que las otras skills
puedan leer sin preguntar nada. Esquema y claves: [../seo/CONFIG-SCHEMA.md](../seo/CONFIG-SCHEMA.md)
y `harnesses/tools/seo/README.md` (§ seo-project.json). Léelos antes de escribir.

## Paso 1 — ¿Existe ya?

Aplica el descubrimiento de CONFIG-SCHEMA. Si hay config: modo **actualizar** — nunca
sobrescribas un campo relleno sin confirmarlo; solo rellenas huecos y `sources_verified`.
Si no: destino = `$ARGUMENTS`, si no `site/config/` cuando exista, si no `.claude/seo-project.json`.

## Paso 2 — Inferir antes de preguntar

Del repo, con Glob/Grep/Read, sin red:

- **dominio / site_url**: `astro.config.*`, `site.json`, `package.json`, CLAUDE.md, README.
- **secretos**: ficheros en `db/secrets/`, `secrets/`, `.env*` que parezcan `brave`, `bing`,
  `serpapi`, `gsc`. Anota la ruta relativa al config.
- **watchers existentes** (`sources.*`): `*serp*watch*.py`, `*gsc*.py`, `*bing*.py` y sus JSONL en `ops/`.
- **docs de estrategia** (`context_docs`): `GROWTH.md`, `*offpage*`, `*audit*`, `*aeo*`, `*competid*`.
- **seeds**: `*keywords*.csv`, `*negativ*`, listas de queries en scripts (`MONEY`, `BRAND`).
- **competidores y metros**: nombres de dominio y ciudades citados en los docs anteriores.
- **plantillas de página** (`page_templates`): rutas de `src/pages/`.
- **activos enlazables**: calculadoras, tablas de precios, datasets públicos.

Completado: cada clave del esquema está o inferida (con su fuente) o marcada "preguntar".

## Paso 3 — Preguntar lo que falta, en un solo bloque

Solo lo no inferible: metros (id, nombre, condado, ciudades, `serpapi_location`), roster de
competidores (dominio, tipo, metro, nota), `positioning` (una frase honesta: qué es y qué **no**
es el negocio), presupuesto mensual por fuente. Propón valores por defecto; el usuario corrige.

## Paso 4 — Escribir

JSON con claves `_x` de justificación (convención del proyecto si la tiene), rutas relativas al
propio fichero, `_schema: "seo-project/1"`. Crea `<ops_dir>/` y un `research-log.jsonl` vacío.
No toques ningún otro fichero del repo.

## Paso 5 — Smoke test por fuente

```
S="<python_bin> <scripts.dir>/seo.py --project <cfg>"
$S probe                                   # Bing kw ×3, Brave ×1, SerpAPI /account
<python_bin> <scripts.gsc_queries> --days 7 --json | head -c 300      # si existe
```

Rellena `sources_verified`: `{"bing_wmt": "2026-09-26", "brave": "missing: db/secrets/brave-api-key.txt", …}`.
Una fuente ausente no bloquea: se anota y las skills la tratarán como `n/d`.

Completado: cada `secrets.*` existe o está marcado `missing`; cada `scripts.*` resuelve a un
fichero o es `null` con motivo en `_scripts`; `probe-<fecha>.json` está en `<ops_dir>`.

## Paso 6 — Cierre

Tres líneas: ruta del config, fuentes OK / ausentes (y cómo conseguir cada clave ausente en
una frase), y `→ /seo-keywords <metro-1>`.
