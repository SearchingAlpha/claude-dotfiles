# Informe SEO — formato, research-log y reglas duras

Absorbe `seo-report` de open-seo. Markdown, no HTML. Una sola casa: las skills apuntan aquí.

## Destino

`<ops_dir>/<tipo>-<sujeto>-<YYYY-MM-DD>.md` — `keywords-tampa-2026-09-26.md`,
`competitors-landscape-orlando-…`, `competitors-midfloridashortloads.com-…`, `rank-…`,
`links-shed-slab-…`. Mismo sujeto y mismo día → sobrescribir. Otro día → fichero nuevo.

## Secciones fijas, en este orden

Título: `# <Tipo> — <sujeto> — <fecha>`.

1. **Veredicto** — 2-3 líneas. La respuesta primero, la implicación de negocio después.
2. **Recomendaciones** — **de 1 a 3**. Cada una: `Problema / Cambio / Efecto esperado / Dónde`
   (fichero, plantilla o URL del repo consumidor). El efecto lleva etiqueta: *medido*, *estimado*
   o *hipótesis*. Un informe con veinte hallazgos ha fallado en priorizar: **ancho → estrecho**.
3. **Evidencia** — tablas ≤ 8 columnas. Cada columna numérica dice su origen en la cabecera
   (`bing_vol`, `gsc_pos_28d`, `brave_pos`, `google_pos`). Sin dato = `n/d`. Nunca vacío,
   nunca inventado. Todo número aparece en texto (no solo en un gráfico).
4. **Lo que no sabemos** — obligatoria. Copia las filas "no da" de DATA-SOURCES.md que afecten
   a este informe (KD, CPC, volumen Google, backlinks de competidor…) y lo que no pudiste verificar.
5. **Siguiente paso** — lista ordenada: acción, skill siguiente, y a qué doc de estrategia
   (`context_docs`) llevar el resultado.
6. **Cómo se hizo este informe** — bloque literal:
   ```
   Fecha · scripts y args ejecutados · llamadas gastadas (brave N, serpapi N, bing_kw N)
   Verificado a mano: <qué páginas se abrieron con WebFetch>
   Metodología derivada de open-seo (MIT) — github.com/every-app/open-seo — reescrita para fuentes gratuitas.
   ```

Estilo: notas, no ensayos. Párrafos de ≤ 2 frases. Sin exclamaciones ni relleno. Un término
técnico se glosa la primera vez (canonical, 301, JSON-LD, crawler). Separa siempre *medido*
(lo devolvió una herramienta) de *inferido* (lo razonaste).

## Después de guardar

En chat, **máximo 3 bullets** (veredicto · recomendación nº 1 con su efecto · acción del
usuario) y la ruta del fichero. No pegues el informe. No narres el proceso.

## Research-log

Append a `<ops_dir>/research-log.jsonl`:

```json
{"ts": "2026-09-26", "skill": "seo-keywords", "subject": "tampa", "verdict": "<1 frase>",
 "report": "<ruta>", "cost": {"brave": 0, "serpapi": 0, "bing_kw": 150}}
```

**Regla 30 días**: antes de gastar una llamada, busca en el log misma `skill` + `subject` en
los últimos 30 días. Si existe, di la fecha y **ofrece reutilizar**; solo con `--fresh` (o un
"sí, repite") vuelves a gastar.

## Cost gate

Antes de cualquier bloque de llamadas de pago: `--dry-run` → tabla `query | fuente | coste`
y `$S quota` (restante). Si el bloque supera `per_run.<fuente>` del config, o si toca SerpAPI,
**espera un "ok"** explícito. Un aviso no es un gate; la espera sí.

## Reglas duras

- **n/d, no invención.** Si la herramienta no devolvió un valor, escribe `n/d` y por qué.
- Nunca reseñas falsas, PBN, enlaces pagados, "backlink blast", outreach masivo.
- Nunca copiar contenido de un competidor: la recomendación es *mejor respuesta al mismo intent*.
- Nunca relanzar los watchers del proyecto (`sources.*`): se **leen**. Si un JSONL tiene
  > 14 días, se avisa ("corre el cron / serp_watch.py") y se usa igual, fechado.
- `positioning` del config es la única descripción del negocio que sale hacia fuera.
