---
name: seo
description: "Router SEO: estado del proyecto (cuota, último informe por skill) y el siguiente paso entre /seo-setup, /seo-keywords, /seo-competitors, /seo-rank y /seo-links. Cero llamadas de pago. Invócala con /seo."
disable-model-invocation: true
argument-hint: "[estado]"
license: "MIT (metodología derivada de open-seo, ver NOTICE.md)"
---

# seo — el router

Familia de skills que replica los resultados de open-seo (keyword research, competidores,
rank tracking, link prospecting) con **fuentes gratuitas**: GSC, Bing Webmaster Tools, Brave
Search API, SerpAPI, Common Crawl. Ninguna skill llama APIs: todas ejecutan
`seo.py` (la librería de `harnesses/tools/seo/`) por Bash y leen JSON de stdout.

Lo que **no** existe gratis y nunca se inventa: keyword difficulty, CPC, volumen Google,
grafo de backlinks de competidores, rank grid local. Tabla en [DATA-SOURCES.md](DATA-SOURCES.md).

Referencia compartida por las cinco skills de trabajo (viven aquí, se leen por ruta):
[CONFIG-SCHEMA.md](CONFIG-SCHEMA.md) (config y descubrimiento), [REPORT-FORMAT.md](REPORT-FORMAT.md)
(informe y research-log), [DATA-SOURCES.md](DATA-SOURCES.md) (lo que da cada fuente).

## Paso 1 — Localizar el config

Sigue la regla de descubrimiento de [CONFIG-SCHEMA.md](CONFIG-SCHEMA.md). Sin config →
imprime solo `Sin seo-project.json. Ejecuta /seo-setup.` y para.

## Paso 2 — Estado (0 llamadas)

Con `S = "<python_bin> <scripts.dir>/seo.py --project <cfg>"`:

- `$S quota --json` → uso del mes por fuente y SerpAPI restante.
- `<ops_dir>/research-log.jsonl` → última línea por `skill` (fecha, `subject`, `verdict`).
- `sources_verified` del config → qué fuentes se probaron y cuándo.

Completado: tienes fecha del último informe de cada una de las 4 skills de trabajo (o "nunca").

## Paso 3 — Una pantalla

Imprime, sin prosa:

```
Proyecto <domain> · config <ruta>
Cuota <mes>: brave X/Y · bing_kw X/Y · serpapi X/Y (cuenta: N, reserva cron R)
Fuentes: gsc ✓ <fecha> · bing_wmt ✓ · brave ✗ falta clave · serpapi ✓
Último informe: keywords <fecha|nunca> · competitors … · rank … · links …
→ Siguiente: /seo-<x> <arg>   (motivo en una línea)
```

**Siguiente paso** = orden canónico `setup → keywords <metro-1> → competitors landscape <metro-1>
→ rank baseline → links <asset>`: el primero sin informe en 30 días. Si todos están al día,
propone `/seo-rank semanal`.

Cierra con la tabla de cuándo usar cada skill:

| Skill | Cuándo |
|---|---|
| `/seo-setup` | primer uso en un repo, o cambió el roster/metros/secretos |
| `/seo-keywords <metro>` | qué atacar en un metro: shortlist, clusters, página destino |
| `/seo-competitors landscape <metro>` \| `<dominio>` | quién gana y por qué; perfil de un rival |
| `/seo-rank baseline\|semanal` | cómo vamos: matriz Brave/Google/Bing/GSC y diff |
| `/seo-links <asset>\|tier2\|tier3` | prospección de enlaces honestos para el activo enlazable |

Completado: la pantalla nombra **un** siguiente paso con su argumento.
