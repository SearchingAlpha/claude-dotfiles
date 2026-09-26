---
name: seo-keywords
description: "Keyword research por metro con fuentes gratuitas (GSC, Bing WMT, Brave): shortlist priorizada, clusters por intención y página destino. Invócala con /seo-keywords <metro|all> [--serpapi N] [--fresh]."
disable-model-invocation: true
argument-hint: "<metro-id|all> [--serpapi N] [--fresh]"
license: "MIT (metodología derivada de open-seo, ver ../seo/NOTICE.md)"
---

# seo-keywords

Absorbe `keyword-research` + `keyword-clustering` de open-seo. Objetivo: de semillas a un
conjunto de oportunidades **priorizado por encaje de negocio e intención, no por volumen**.
Lee entero antes de empezar: [../seo/CONFIG-SCHEMA.md](../seo/CONFIG-SCHEMA.md),
[../seo/REPORT-FORMAT.md](../seo/REPORT-FORMAT.md), [INTENT-RULES.md](INTENT-RULES.md).

Sujeto = `<metro>` (`id` o `name` de `geo.metros`; `all` = todos). Regla 30 días de
REPORT-FORMAT con `skill: seo-keywords`, `subject: <metro>`; `--fresh` la salta.

## Paso 1 — Semillas (0 llamadas)

`$S seeds gen --metro <metro> --max 60 --include-ads --json` → candidatos con `intent_guess`.
Añade a mano las semillas que el usuario haya dado en `$ARGUMENTS` o en la conversación.
Excluye las queries de `queries.money` y `queries.brand`: ya las mide el proyecto cada semana
(`sources.serp_watch_jsonl`); las lees en el paso 5, no las vuelves a pagar.

Completado: ≤ 60 candidatos, ninguno con token de `negatives_file`, cada uno con intención.

## Paso 2 — Striking distance (GSC, 0 llamadas de pago)

Si `scripts.gsc_queries` existe: `<gsc_queries> --days 90 --json`. Marca *striking distance*
las filas con posición 5-30 e impresiones ≥ 3; son la primera prioridad (Google ya nos muestra).
Cruza con los candidatos del paso 1 (misma query o misma página servida). Sin GSC → `n/d` y sigue.

Completado: lista de striking distance con `gsc_pos_90d`, `gsc_imp`, página servida.

## Paso 3 — Volumen (Bing)

`$S kw-research --seeds <fichero de los candidatos> --json` (una llamada por keyword; `--related`
solo si el usuario lo pide, aporta poco). Columna `bing_vol` = impresiones Bing/mes. **Nunca**
la conviertas a "volumen Google"; un 0 significa "por debajo del umbral de Bing", no "nadie
busca". KD/CPC/competition llegan `null`: así se quedan.

Completado: cada candidato tiene `bing_vol` numérico o `n/d` (error citado).

## Paso 4 — Cost gate → SERPs de validación

Elige ≤ 15 keywords donde la intención es ambigua o no hay dato GSC. `$S serp -q … --dry-run`
→ tabla `query | motor | coste`; `$S quota`. Brave primero (metro en el texto). SerpAPI
(`--engine google` con geo) solo si `--serpapi N` > 0 y para las N más valiosas. **Espera "ok"**
si supera `per_run.brave` o toca SerpAPI. Luego `$S serp -q … --json`.

Completado: para cada keyword validada, top-5 dominios y **tipo de ganador** (agregador nacional /
operador local / UGC / gobierno / publisher).

## Paso 5 — Clustering y página destino

Agrupa por intención según INTENT-RULES (cost / near-me / pain-point / project / seasonal /
brand). Cada cluster → **keyword primaria** + página destino de `page_templates`
(`T3 /states/<state>/<county>/<city>`…) o "nueva página" con plantilla propuesta.
**Canibalización** = mismo intent servido por ≥ 2 URLs propias en GSC → se señala.
Lee `sources.serp_watch_jsonl` (último snapshot) para las money queries: quién gana, y si hay
AI Overview y a quién cita.

Completado: cada cluster tiene primaria, intención, página destino (o propuesta) y prioridad.

## Paso 6 — Priorizar

Orden: encaje con el negocio (`positioning`) × claridad de intención × evidencia (striking
distance > bing_vol > 0 > solo plantilla). Dificultad = tipo de ganador del SERP, declarado como
proxy (DATA-SOURCES). Near-me pre-supply: etiquetar, no priorizar (INTENT-RULES).

## Paso 7 — Informe

Según REPORT-FORMAT: `keywords-<metro>-<fecha>.md`. En Evidencia: **shortlist ≤ 10** (`keyword |
intent | bing_vol | gsc_pos_90d | ganador SERP | página destino | prioridad`), tabla de clusters,
canibalizaciones, lista larga. Propón añadir las 3-5 mejores a seguimiento con
`$S rank-track add "<q>"` **solo tras confirmación explícita**. Research-log. Chat: 3 bullets.

Completado: cada fila de la shortlist tiene destino; sección 4 lista KD, CPC y volumen Google.
