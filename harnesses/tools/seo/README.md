# seo/ — herramientas SEO con fuentes gratuitas (stdlib-only)

Réplica de lo que da [open-seo](https://github.com/every-app/open-seo) (MIT) sin
DataForSEO. El código de open-seo es gratis; los **datos** no. Esto usa lo que sí
lo es: Bing Webmaster Tools (volumen de cualquier keyword, enlaces propios), Brave
Search API ($5/mes de crédito ≈ 1.000 queries), SerpAPI (250/mes), Common Crawl y
los JSONL que ya produce el proyecto (GSC, serp_watch). Sin MCP: las skills
`/seo-*` de este plugin llaman a `seo.py` por Bash y leen el JSON de stdout.

**Lo que no existe gratis y no se inventa:** keyword difficulty, CPC, volumen
Google, grafo de backlinks de competidores, rank grid local. En las salidas van
como `null`; en los informes, en "Lo que no sabemos".

## Uso

```bash
S="python $HOME/claude-dotfiles/harnesses/tools/seo/seo.py"   # ~ dentro de comillas NO se expande
$S probe --project site/config/seo-project.json                # paso 1 en un proyecto nuevo
$S serp --queries money --engine brave --dry-run
$S rank-track run && $S rank-track history
$S report --format md --write-matrix --out site/ops/seo/report-$(date +%F).md
```

Flags globales: `--project PATH` (default: `./seo-project.json` → `./site/config/` →
`./config/` → `./.claude/` → `$SEO_PROJECT`), `--dry-run` (llamadas y coste, sin red),
`--force` (salta el guard de cuota), `--json` (resultado a stdout; lo humano va a
stderr), `--only SUBSTR` (reanudar), `--limit N`.
Salida: 0 ok · 1 config/secreto · 2 todo falló · 3 cuota rechazada.

Requisitos: Python ≥ 3.9, sin dependencias. El repo debe estar (o enlazado) en
`~/claude-dotfiles` (en Windows: `mklink /J %USERPROFILE%\claude-dotfiles <ruta>`).

## Subcomandos

| Subcomando | Fuente | Escribe en `<ops_dir>/` | Coste |
|---|---|---|---|
| `probe [--bing-date-style iso\|ms\|both] [--keyword K]` | Bing kw, Brave, SerpAPI /account | `probe-<fecha>.json` | Brave 1, Bing 3-4 |
| `seeds gen [--max 150] [--from-db N] [--include-ads] [--metro M]` | plantillas del config (+sqlite, +CSV ads, −negativas) | `seeds.json` | 0 |
| `kw-research [--seeds F\|-] [--months 3] [--related] [--max-calls N]` | Bing `GetKeyword` (+`GetRelatedKeywords`) | `kw-research.jsonl` | Bing 1-2/seed |
| `serp [--queries money\|brand\|tracking\|all] [--engine brave\|google\|bing] [--depth 20] [-q "…"] [--max-age-days N]` | Brave / SerpAPI | `serp.jsonl` | 1/query |
| `competitors [--days 30] [--fetch]` | `serp.jsonl` + `sources.serp_watch_jsonl` | `competitors.jsonl` | 0 |
| `rank-track add "q"` · `list` | edita `queries.tracking` del config (conserva `_x`) | config | 0 |
| `rank-track run [--engine brave] [--queries all]` | = serp + posición propia | `serp.jsonl`, `rank-history.jsonl` | 1/query |
| `rank-track history [--query S] [--last N]` · `diff [--engine]` | rank-history + serp_watch + gsc | stdout | 0 |
| `backlinks-own [--pages 20] [--ahrefs-csv F]` | Bing `GetLinkCounts`/`GetUrlLinks` o CSV Ahrefs WT | `backlinks-own.jsonl` | 0 (WMT) |
| `mentions [--target DOM] [--depth 20]` | Brave `"dom" -site:dom`, marca, teléfono | `mentions.jsonl` | Brave 2-5 |
| `cc-check [--domains …]` | Common Crawl index | `cc-check.jsonl` | 0 |
| `report [--sections rank,competitors,kw,links,mentions] [--format md\|json] [--write-matrix] [--out F] [--top 30]` | todos los JSONL | stdout / `rank-matrix.json` | 0 |
| `quota` | `seo-quota.json` + SerpAPI /account | stdout | 0 |

Toda llamada de pago pasa por `quota_guard` (techo mensual en `budgets`) y se apunta
en `seo-quota.json`. SerpAPI además respeta `serpapi_reserve` (lo que necesita el cron
del proyecto). Un fallo por ítem no tira el pase: queda en `errors[]`.

## `seo-project.json`

Rutas relativas al directorio del propio JSON. Claves `_x` = justificación (se
conservan al reescribir). Obligatorias: `domain`, `ops_dir`, `geo.country`,
`geo.language`, `queries.money`. Ejemplo completo:
`~/workplace/agentic-marketplace/site/config/seo-project.json`.

```json
{
  "_schema": "seo-project/1",
  "domain": "example.com", "site_url": "https://example.com",
  "brand": {"name": "Example", "tokens": ["example"], "phone": null},
  "python_bin": "python3", "ops_dir": "../ops/seo",
  "scripts": {"dir": "~/claude-dotfiles/harnesses/tools/seo", "gsc_queries": null},
  "secrets": {"brave": "../../secrets/brave-api-key.txt", "bing_wmt": "../../secrets/bing-api-key.txt", "serpapi": null},
  "budgets": {"brave": 900, "serpapi": 0, "bing_kw": 300}, "serpapi_reserve": 0,
  "per_run": {"brave": 40, "serpapi": 10},
  "geo": {"country": "US", "language": "en", "bing_language": "en-US", "state": "FL",
          "default_location": "Florida, United States",
          "metros": [{"id": "tampa", "name": "Tampa", "county": "hillsborough",
                      "serpapi_location": "Tampa, Florida, United States", "cities": ["Brandon"]}],
          "metros_db": null},
  "queries": {"money": ["…"], "brand": ["…"], "tracking": []},
  "competitors": [{"domain": "rival.com", "type": "operator-local", "metro": "tampa", "note": ""}],
  "domain_classes": {"aggregator": ["angi.com"], "ugc": ["reddit.com"]},
  "seeds": {"heads": ["…"], "quantities": [], "projects": [],
            "modifiers": {"transactional": ["delivery"], "commercial": ["cost"], "informational": []},
            "geo_forms": ["{metro}", "{metro} fl"], "negatives_file": null, "ads_keywords_csv": null},
  "sources": {"serp_watch_jsonl": null, "gsc_watch_jsonl": null, "gsc_queries_jsonl": "../ops/seo/gsc-queries.jsonl"},
  "context_docs": [], "positioning": "", "sources_verified": {}
}
```

- `geo.metros[].cities` alimenta las plantillas de seeds y la detección de geo por query.
- `sources.*` son JSONL que produce el propio proyecto; `report` y `rank-track history`
  los fusionan sin gastar. GSC queda fuera de esta librería (necesita PyJWT): el
  proyecto aporta `scripts.gsc_queries` (ejemplo: `site/scripts/gsc_queries.py` en ShortLoad)
  que escribe `sources.gsc_queries_jsonl`.

## Esquemas de salida (espejo de open-seo)

- **`kw-research.jsonl`** (1 línea/pase): `{ts, run_id, source:"bing_wmt", window, months, seeds_n,
  calls, rows:[{keyword, searchVolume, broadVolume, volumeSource, volumeNote, keywordDifficulty:null,
  cpc:null, competition:null, intent, metro, county, seed, origin:"head|template|related|ads"}], errors}`.
  `searchVolume` = impresiones Bing exactas / meses. `cpc` solo si la keyword está en el CSV de ads
  (`cpcSource:"ads_bid"`, es nuestra puja, no mercado).
- **`serp.jsonl`**: `{ts, run_id, engines, results:[{engine, query, geo, ok, items:[{rank, domain, url,
  title, description, type}], n, our_rank, our_url}]}`.
- **`rank-history.jsonl`** (1 línea/query×pase): `{ts, engine, query, geo, rank|null, url, top3, n}`.
- **`competitors.jsonl`**: `{ts, window_days, queries_n, engines, domains:[{domain, class, score,
  appearances, queries_won, best_rank, avg_rank, queries, engines, known}]}`. Score = Σ (21−rank)/20.
  `class` ∈ competitor | brand_collision | aggregator | ugc | other. `known:false` = fuera del roster.
- **`backlinks-own.jsonl`**: `{ts, source:"bing_wmt|ahrefs_csv", overview:{backlinks, referringDomains,
  referringPages, rank:null}, referringDomains:[{domain, backlinks, referringPages, targets, dr?}], note}`.
- **`mentions.jsonl`**: `{ts, target, queries, hits:[{query, rank, url, domain, title, self}],
  independent_count, independent_domains}`.
- **`rank-matrix.json`** (`report --write-matrix`): `{ts, rows:[{query, brave, brave_prev, google, bing,
  gsc_pos, gsc_imp, gsc_page}], sources, diff}`.
- **`seo-quota.json`**: `{"YYYY-MM": {brave, serpapi, bing_kw}}`.

## Intención (heurística, `core.classify_intent`)

navigational (token de `brand.tokens`) → informational (`how to|what is|when|why|best months|guide`)
→ commercial (`cost|price|how much|minimum|per yard|vs|cheap`) → transactional (`delivery|near me|
order|supplier|quote` o un metro/ciudad del config) → informational por defecto.

## Fuentes: lo verificado (2026-09-26)

| Fuente | Da | No da | Cuota | Notas |
|---|---|---|---|---|
| Bing WMT keyword (`GetKeyword`) | impresiones exactas y broad de **cualquier** keyword, ventana ≤ 90 d | volumen Google, KD, CPC | no documentada | `country=us` (minúsculas), `language=en-US`, fechas ISO. "US"/"en" → 400. Long-tail hiperlocal → 0 (esperado); heads ("concrete delivery") sí. `GetRelatedKeywords` devuelve poco. |
| Bing WMT enlaces | backlinks del **propio** dominio | competidores | — | `GetLinkCounts` vacío si Bing aún no rastrea. |
| Brave Search API | top-20 orgánico | geo por ciudad, AI Overview | ~1.000/mes (plan Free, tarjeta sin cargo), 1 req/s | el metro va en el texto; "near me" inútil. 429 → backoff 20-90 s. |
| SerpAPI | Google/Bing geolocalizado + AI Overview | — | 250/mes | la librería no lo usa por defecto; `serpapi_reserve` protege el cron. |
| Common Crawl | ¿está el dominio en el último crawl? | — | gratis | 504 a ratos; reintenta. |
| GSC (vía script del proyecto) | posición/impresiones propias por query×página | queries sin impresión | — | fuente de striking distance. |

## Playbook en un proyecto nuevo

1. Clave Brave en `secrets.brave`; Bing WMT API key en `secrets.bing_wmt` (sitio verificado).
2. `seo.py probe` → decide si Bing responde; anota formato.
3. `seo.py rank-track run` (Brave, ~16 llamadas) → `rank-track history`.
4. `seo.py competitors --days 30` (0 llamadas si ya hay serp.jsonl).
5. `seo.py seeds gen --max 150 --include-ads` → `seo.py kw-research --related`.
6. `seo.py backlinks-own` · `seo.py mentions` · `seo.py cc-check`.
7. `seo.py report --write-matrix --out <ops_dir>/report-<fecha>.md`.

Cadencia sugerida: `rank-track run` semanal (≈16 Brave), `competitors` semanal (0),
`mentions` quincenal (≈5), `kw-research` mensual (≈150-300 Bing).

## Módulos

`seo.py` (CLI) · `core.py` (config, http, jsonl, cuota, intención) · `clients.py` (brave,
bingkw, bingwmt, serpapi, cc) · `cmd_probe.py` · `cmd_seeds.py` · `cmd_kw.py` · `cmd_serp.py`
(serp + competitors) · `cmd_rank.py` · `cmd_links.py` (backlinks-own, mentions, cc-check) ·
`cmd_report.py` (report, quota). Convención: `from __future__ import annotations`, sin
`requests`, un fallo por ítem no tira el pase, JSONL se escribe en `finally`.

Metodología derivada de open-seo (MIT, Copyright (c) 2026 Ben Senescu); reescrita para
fuentes gratuitas. Ver `skills/seo/NOTICE.md`.
