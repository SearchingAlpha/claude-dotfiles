---
name: seo-rank
description: "Rank tracker con fuentes gratuitas: matriz Brave / Google / Bing / GSC por query, diff contra el pase anterior, striking distance y línea sugerida para los docs del proyecto. Invócala con /seo-rank baseline|semanal|<query>."
disable-model-invocation: true
argument-hint: "baseline | semanal | <query> [--serp]"
license: "MIT (metodología derivada de open-seo, ver ../seo/NOTICE.md)"
---

# seo-rank

Cómo vamos, semanal, barato. Brave paga ~16 llamadas; Google/Bing y GSC se **leen** de lo que
el proyecto ya produce. Lee entero: [../seo/CONFIG-SCHEMA.md](../seo/CONFIG-SCHEMA.md),
[../seo/REPORT-FORMAT.md](../seo/REPORT-FORMAT.md).

Modos: `baseline` (primer pase; solo fija el punto de partida), `semanal` (pase + diff),
`<query>` (histórico de una sola query, 0 llamadas). `--serp`: además Google geolocalizado vía
SerpAPI para ≤ 5 money queries (cost gate obligatorio).

## Paso 1 — Set y frescura

`$S rank-track list --json` → money + brand + tracking. Comprueba la fecha del último snapshot de
`sources.serp_watch_jsonl` y de `sources.gsc_queries_jsonl`: si > 14 días, avísalo en el informe
("corre `weekly.sh` / `<gsc_queries>`"), no los relances tú. Si `<gsc_queries>` existe y su JSONL
tiene > 7 días, ejecútalo (GSC no gasta): `<gsc_queries> --days 28 --json`.

## Paso 2 — Cost gate → pase Brave

`$S rank-track run --dry-run` → N llamadas Brave. Si N > `per_run.brave`, espera "ok".
`$S rank-track run --json`. Modo `<query>`: salta este paso.

Completado: `rank-history.jsonl` tiene una fila por query del set con `ts` de hoy (o error citado).

## Paso 3 — Matriz y diff (0 llamadas)

`$S rank-track history --json` → por query: `brave`, `brave_prev`, `google`, `bing`, `gsc_pos`,
`gsc_imp`, `gsc_page`. `$S rank-track diff --json` → eventos `entered | left | up | down | same |
new_query`. `$S report --sections rank --write-matrix` deja `rank-matrix.json` para el dashboard
del proyecto.

Lee además el último snapshot de `serp_watch` para las money queries: `answer_box` y
`ai_overview` (presente, nos cita, a quién cita).

## Paso 4 — `--serp` (opcional, SerpAPI)

Solo money queries, ≤ 5, con geo del metro: `$S serp --queries money --engine google --dry-run`
→ coste y `$S quota` (respeta `serpapi_reserve`). **Espera "ok"**. Luego sin `--dry-run`.

## Paso 5 — Lectura

- **Movimientos** ≥ 3 posiciones (up/down) y entradas/salidas del top-20.
- **Página servida cambió** en GSC para la misma query → posible canibalización → `/seo-keywords`.
- **Queries nuevas con impresiones** en GSC que no están en el set → candidatas a `rank-track add`.
- **Striking distance** (GSC pos 5-30, ≥ 3 imp) → entrada de `/seo-keywords`.
- AI Overview: presente / nos cita / cita a quién.

## Paso 6 — Informe

`rank-<fecha>.md` (o `rank-<query>-<fecha>.md`). Evidencia: la matriz completa (`query | brave |
prev | google | bing | gsc_pos | gsc_imp | página`), tabla de eventos del diff, striking distance.
`baseline`: Veredicto = "punto de partida fijado", Recomendaciones = solo qué añadir al set.
Añade dos líneas sugeridas, **sin escribirlas tú** en los docs del proyecto: una fila para el
log de AEO del proyecto (`sources.aeo_log`) y un bullet de estado para el doc de crecimiento
(`context_docs`). Research-log con `subject: baseline|semanal|<query>`. Chat: 3 bullets.

Completado: cada query del set tiene fila con dato o `n/d` explicado; el diff nombra el pase
anterior por fecha; ninguna llamada SerpAPI sin "ok".
