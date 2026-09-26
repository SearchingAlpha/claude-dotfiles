# Checklist de citabilidad — qué mirar en una página ganadora (WebFetch)

Una fila por página. Rellena solo lo **visto**; lo que no se pudo ver (403, JS) → `n/d`.

| Campo | Cómo verlo | Por qué importa |
|---|---|---|
| `title` / `meta description` literales | `<title>`, `<meta name="description">` | ¿la meta es la respuesta literal a la query? |
| Tipos de schema | `<script type="application/ld+json">` → `@type` | LocalBusiness, Product/Offer, FAQPage, HowTo, Article, BreadcrumbList |
| Precios publicados y **fechados** | tablas, "$X/yd", "as of <mes>" | el slot de retrieval lo gana el primer origen fechado |
| Autor / responsable / fecha de actualización | byline, "updated", `dateModified` | E-E-A-T visible |
| FAQ visible y `FAQPage` coherentes | bloque FAQ vs schema | divergencia = señal negativa |
| Cobertura geo | ¿página por ciudad/condado o una genérica? | granularidad frente a la nuestra |
| `llms.txt` / `llms-full.txt` | `/llms.txt` | ¿instruye a los LLM cuándo citarlo? (patrón concretenetwork) |
| `robots.txt` bots IA | GPTBot, ClaudeBot, PerplexityBot, Google-Extended | bloquear = auto-excluirse de los LLM (hueco para nosotros) |
| Bloqueo a fetchers | HTTP 403/202/JS shell | agregadores suelen bloquear: posición medida, anatomía `n/d` |
| Activo enlazable | calculadora, tabla, dataset, guía con datos | qué les enlazan (y qué podríamos superar) |
| Reseñas / NAP / `sameAs` | LocalBusiness, perfiles enlazados | entidad corroborada |
| Antigüedad del dominio | RDAP / whois (`rdap.org/domain/<dom>`) | explica autoridad que no es contenido |

## Patrones "robables" (se roba el patrón, nunca el texto)

- Meta description = respuesta literal con número y fecha.
- Tabla de precios por ciudad con "(estimated)" honesto y fecha.
- FAQ money-query por ciudad, mismo array que el schema.
- `llms.txt` con sección "cite this page when…".
- Página explainer del *pain-point* (fee, minimum) con tabla comparativa.

## Vulnerabilidades típicas, ordenadas por ganabilidad

1. Sin precios ni rangos (solo "call for quote").
2. Sin schema o schema roto.
3. Página única para todo el estado (sin granularidad geo).
4. Sin autor/fecha; contenido de 2019.
5. Bloquea bots IA (fuera de AI Overviews y LLM).
6. Colisión de marca con otro estado (navigational ajena).
