# Fuentes — qué da cada una, qué no, y cuánto cuesta

Única casa de "lo que no sabemos". La sección 4 de cada informe copia de aquí.

| Fuente | Da | No da | Cuota | Subcomando |
|---|---|---|---|---|
| **GSC** (script del proyecto, `scripts.gsc_queries`) | posición media, impresiones, clics y página servida de cada query donde Google **ya** nos mostró; *striking distance* (pos 5-30 con impresiones) | queries sin impresión (no dice quién gana ese SERP) | ilimitada | `<gsc_queries> --days 28\|90 [--contains] --json` |
| **Bing WMT · keywords** | impresiones exactas (`Impressions`) y amplias (`BroadImpressions`) de **cualquier** keyword, ventana ≤ 90 d; `related` (pobre) | volumen Google, KD, CPC, competencia | no documentada; parar al primer 429/403 | `kw-research`, `probe` |
| **Bing WMT · enlaces** | backlinks al dominio **propio** (páginas con inlinks, fuentes, anchors) | backlinks de terceros; DR/DA | — | `backlinks-own` |
| **Brave Search API** | top-20 orgánico por query (`rank, url, domain, title, description`) | geo por ciudad (el metro va en el texto; "near me" inútil), AI Overview | ≈ 1.000/mes (plan Free, tarjeta sin cargo), 1 req/s; 429 → backoff 20-90 s | `serp --engine brave`, `rank-track run`, `mentions` |
| **SerpAPI** | Google/Bing geolocalizados + AI Overview (quién cita) | — | 250/mes, **compartida con el cron del proyecto** (`serpapi_reserve`) | `serp --engine google\|bing`; normalmente se **lee** `sources.serp_watch_jsonl` |
| **Ahrefs Webmaster Tools** (CSV manual) | backlinks propios con DR, contraste de Bing | terceros; API | manual | `backlinks-own --ahrefs-csv` |
| **Common Crawl** | ¿está el dominio en el último crawl? (proxy de "existe para los corpus") | nada más | gratis, 504 a ratos | `cc-check` |
| **WebFetch** (tú) | anatomía de una página: title/meta, schema, precios, autor, fechas, llms.txt, robots | JS-rendered, sitios que bloquean bots (403) | — | verificación a mano; se declara en "Cómo se hizo" |

## No disponible gratis (siempre en "Lo que no sabemos")

- **Keyword difficulty** → proxy declarado: *tipo de ganador del SERP* (top-5 agregadores nacionales
  = difícil; operadores locales con páginas finas = alcanzable). Es una inferencia, se etiqueta.
- **CPC / competencia de pago** → solo la puja propia si la keyword está en el CSV de ads
  (`cpcSource: ads_bid`), que es nuestra oferta, no el mercado.
- **Volumen Google** → `bing_vol` ≈ 1/8, sirve para ordenar, no para dimensionar. La long-tail
  hiperlocal da 0 en Bing aunque exista en Google: "0" significa "por debajo del umbral de Bing".
- **Backlinks de competidores** → proxy: `mentions --target <dom>` (menciones en Brave) y
  `cc-check`. Nunca "referring domains" de un rival.
- **Rank grid local / Maps** → fuera de alcance hasta tener perfil GBP activo.
- **AI Overview / citas en LLMs** → solo vía SerpAPI (Google) o el pase manual del proyecto.
