---
name: seo-links
description: "Link prospecting honesto con fuentes gratuitas: para un activo enlazable (o un tier del plan off-page) encuentra páginas que enlazarían, su vía de contacto con fuente, el ángulo y el borrador. Invócala con /seo-links <asset-url|tier2|tier3> [metro]."
disable-model-invocation: true
argument-hint: "<asset-url|tier2|tier3> [metro] [--fresh]"
license: "MIT (metodología derivada de open-seo, ver ../seo/NOTICE.md)"
---

# seo-links

Absorbe `link-prospecting` de open-seo. Prospectos **realistas** para un activo concreto, con
contacto **encontrado y citado** o "no encontrado + siguiente vía". Bucle con estado: el
pipeline vive en `<ops_dir>/link-prospects.jsonl` y cada run lo muestra antes de buscar más.
Lee entero: [../seo/CONFIG-SCHEMA.md](../seo/CONFIG-SCHEMA.md),
[../seo/REPORT-FORMAT.md](../seo/REPORT-FORMAT.md), [OUTREACH-TEMPLATES.md](OUTREACH-TEMPLATES.md).

Línea roja (del plan off-page del proyecto y de open-seo): nada de PBN, enlaces pagados,
granjas, spam en foros, "backlink blast", outreach masivo. Un prospecto que huela a pago se
**marca**, no se contacta.

## Paso 1 — Contexto y pipeline

Lee los `context_docs` que casen `*offpage*`/`*autoridad*`: tiers, restricción de honestidad
(`positioning`: qué es y qué **no** es el negocio), qué está hecho. **Tier 1** (perfiles de
marca: LinkedIn, Bing Places, Crunchbase…) es checklist manual del usuario: no se prospecta, se
recuerda si está a 0. Muestra el pipeline actual de `link-prospects.jsonl` (`status` por
prospecto). Regla 30 días con `subject` = asset o tier.

## Paso 2 — El activo y su afirmación citable

`<asset-url>` de `linkable_assets` (o `tier2`/`tier3` → elige el activo más citable). Escribe en
una línea **por qué merece cita**: dato fechado que nadie publica, herramienta, tabla por
condado, comparación. Sin esa línea no hay outreach.

## Paso 3 — Queries de prospección → cost gate → Brave

5-10 queries, localizadas: `<topic> resources florida` · `<county> chamber of commerce members` ·
`florida home improvement blog` · `<competitor> alternatives` · `<topic> cost <metro> guide` ·
`<topic> statistics` · `<topic> calculator` · `best <category> <metro>`. `$S serp -q … --dry-run`
→ coste; espera "ok" si > `per_run.brave`. `$S serp -q … --engine brave --json`.

## Paso 4 — Cualificar

**Mantener**: páginas editoriales, recursos, directorios honestos (cámara, asociaciones,
gobierno local, universidades/extensión), listas curadas, comparativas, publishers del tema.
**Descartar o marcar**: homepages, login, thin affiliates, spam, competidores directos (salvo
ángulo comparación válido), placements de pago. `$S backlinks-own --json` → excluye dominios que
ya enlazan (y sirve de medición en runs siguientes).

Completado: cada prospecto mantenido tiene tipo, URL concreta (no homepage) y motivo.

## Paso 5 — Contacto, solo lo encontrado

Con WebFetch en la página y el sitio: byline/autor, `/contact`, `/about`, masthead,
directrices editoriales, JSON-LD `Person`/`Organization`/`sameAs`/`email`, perfiles
profesionales. **Registra solo lo visto, con URL fuente.** Nunca inventes un email, un handle
ni un nombre. Sin contacto → "siguiente vía" concreta (formulario X, LinkedIn de Y, teléfono
en Z).

## Paso 6 — Ángulo y borrador

Un ángulo por prospecto: recurso mejor · dato más actual · herramienta útil · inclusión en
comparación/lista · cita experta. Borrador en el idioma del prospecto desde
OUTREACH-TEMPLATES, con la disclosure fija y el `positioning`. Personalizado por página y
motivo; sin plantilla masiva.

## Paso 7 — Informe y pipeline

`links-<asset>-<fecha>.md`: **El ángulo** (2 líneas) · Prospectos (tabla ≤ 8 columnas: `URL |
sitio | tipo | ángulo | contacto | fuente del contacto | prioridad | status`) · Por qué estos ·
Borradores (2-3 reutilizables) · Limitaciones (sin contacto, competidores, pago) · Siguiente
paso (orden de envío) · Cómo se hizo. Upsert en `link-prospects.jsonl`:
`{domain, url, tier, asset, angle, contact, contact_source, status: nuevo|contactado|conseguido|descartado, updated}`.
Research-log. Chat: 3 bullets.

Completado: cada prospecto tiene contacto+fuente o "no encontrado + vía"; ningún email sin
URL; los de pago están marcados; el pipeline refleja este run.
