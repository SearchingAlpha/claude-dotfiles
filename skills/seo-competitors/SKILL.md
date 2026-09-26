---
name: seo-competitors
description: "Competidores con fuentes gratuitas: `landscape <metro>` (quién gana cada intención y por qué) o `<dominio>` (perfil de un rival: dónde aparece, anatomía de sus páginas, vulnerabilidades). Invócala con /seo-competitors."
disable-model-invocation: true
argument-hint: "landscape <metro> | <dominio> [--fresh]"
license: "MIT (metodología derivada de open-seo, ver ../seo/NOTICE.md)"
---

# seo-competitors

Absorbe `competitive-landscape` y `competitor-analysis` de open-seo. Dos ramas; lo común
primero. Lee entero: [../seo/CONFIG-SCHEMA.md](../seo/CONFIG-SCHEMA.md),
[../seo/REPORT-FORMAT.md](../seo/REPORT-FORMAT.md), [CITABILITY-CHECKLIST.md](CITABILITY-CHECKLIST.md).

**Sin backlinks de competidor.** No existen gratis (DATA-SOURCES). El proxy es `mentions
--target` y `cc-check`; se dice así en el informe. Todo hallazgo lleva etiqueta *medido*
(lo devolvió serp/competitors/WebFetch) o *inferido*.

## Común

1. Config; regla 30 días (`subject` = metro o dominio); `--fresh` la salta.
2. Lee `context_docs` que hablen de competidores (auditorías previas): son tu **baseline** para el diff.
3. `$S competitors --days 30 --json` → tally actual (0 llamadas: usa `serp.jsonl` + `serp_watch`).
   Si `queries_n` < 8 o el último pase tiene > 14 días, la rama decide si refresca (cost gate).

## Rama `landscape <metro>`

4. **Query set**: 5-10 queries del metro cubriendo las 5 intenciones de
   `../seo-keywords/INTENT-RULES.md` (toma de `queries.*` + `seeds gen --metro`). Excluye money/brand
   (ya en serp_watch).
5. **Cost gate** → `$S serp -q … --engine brave --max-age-days 14 --json` (reusa lo de < 14 días).
6. `$S competitors --days 30 --json` de nuevo → dominios por score; **clase** por config;
   `known: false` = nuevo → candidato a entrar en el roster.
7. **Perfila el top 3-5** (excluye UGC y gobierno salvo que ganen una money query) con WebFetch
   según CITABILITY-CHECKLIST. Para cada uno: qué gana, por qué (medido), patrón robable.
8. **Huecos**: intenciones sin ganador local (top-5 solo agregadores/UGC) = donde una respuesta
   estructurada propia compite hoy.
9. Informe `competitors-landscape-<metro>-<fecha>.md`: Evidencia = tabla de dominios (`dominio |
   clase | score | #1 | mejor | queries`), tabla de perfiles (checklist), lista de huecos, **diff
   contra el baseline** (quién entró/salió del top-3 por query). Recomendaciones ≤ 3.
   Propón añadir los `nuevo` relevantes a `competitors[]` del config (con confirmación).

Completado: cada dominio del top-10 tiene clase; cada perfil tiene ≥ 1 patrón robable *medido*;
el diff nombra el baseline por fecha.

## Rama `<dominio>`

4. **Dónde aparece**: filtra `competitors.jsonl` por el dominio (queries, mejor posición). Si
   sale en < 3 queries, cost gate → `$S serp -q "site:<dominio> <tema>" -q "<head> <su metro>" …`
   (≤ 10, Brave).
5. **Anatomía** de sus 3-5 páginas ganadoras con WebFetch (CITABILITY-CHECKLIST): title/meta,
   schema, precios y fechas, autor, FAQ, llms.txt, robots (¿bloquea GPTBot/ClaudeBot?).
6. **Head-to-head**: para las queries compartidas, nuestra posición desde `<gsc_queries>
   --contains` y `serp_watch`; su posición desde serp. Tabla `query | ellos | nosotros (gsc) |
   página nuestra`.
7. **Menciones**: `$S mentions --target <dominio> --json` (Brave, 2 llamadas) y `$S cc-check
   --domains <dominio>`. Es el único proxy de autoridad; se etiqueta como tal.
8. **Vulnerabilidades** ordenadas por *ganabilidad*: sin precios fechados, sin schema, sin
   FAQ, thin en T3, bloquea bots IA, sin autor. **Fortalezas** que no se copian: se superan con
   mejor respuesta al mismo intent (nunca copiar contenido).
9. Informe `competitors-<dominio>-<fecha>.md`: Snapshot (tabla dónde aparece) · **La lección
   más grande** (un hallazgo) · Vulnerabilidades · Head-to-head · Menciones/CC · Recomendaciones ≤ 3.
   Propón actualizar la `note` del dominio en `competitors[]` (con confirmación).

Completado: cada vulnerabilidad cita la URL donde se vio; sección 4 dice "backlinks de
<dominio>: no disponible".
