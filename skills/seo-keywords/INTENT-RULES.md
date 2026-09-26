# Intención, expansión, dificultad-proxy y clustering

## Cinco clases de intención (para clusters; la librería usa una heurística más gruesa)

| Clase | Señal en la query | Ejemplo | Página tipo |
|---|---|---|---|
| **cost** | cost, price, how much, per yard, delivered, quote | "2 yards concrete delivered cost tampa" | T3/T4 con rango de precio fechado; página índice de precios |
| **near-me** | near me, delivery <ciudad>, supplier <ciudad> | "short load concrete delivery near brandon fl" | T3 ciudad (solo gana con operador real → *pre-supply: etiquetar, no priorizar*) |
| **pain-point** | fee, minimum, no minimum, small amount, small batch | "concrete delivery small load no short load fee" | explainer + tabla de fees (activo enlazable) |
| **project** | shed slab, fence posts, patio, footings, driveway, pad, "how much concrete for" | "how much concrete for 10x12 shed slab" | calculadora T5 + T4 producto |
| **seasonal / how-to** | best months, when, how to, can i, what is | "best months to pour concrete in hillsborough county" | página informativa por condado con datos locales |
| brand | tokens de `brand.tokens` | "getshortload" | home; se sigue, no se trabaja |

Una query con señal *cost* + metro es **cost** (no near-me). Ante empate, la clase que más
determina la página destino.

## Plantillas de expansión (lo que ya hace `seeds gen`; aquí para añadir a mano)

`<head> <modificador>` · `<head> <modificador> <ciudad>` · `<head> <modificador> <condado> county`
· `<cantidad> delivered [cost] <ciudad|estado>` · `concrete for <proyecto> delivery <ciudad>` ·
`<informacional> <estado|condado>`. Filtra siempre con `negatives_file` (bags, rental, pump…).

## Dificultad — proxy declarado (no hay KD)

Mira el **tipo de ganador** en el top-5 del SERP (Brave o serp_watch):

- Todo agregadores nacionales (angi, homeadvisor, homeguide, yelp) → **difícil**; solo con activo
  enlazable o dato fechado que ellos no tengan.
- Operadores locales con páginas finas (sin schema, sin precios, sin fecha) → **alcanzable**
  con mejor respuesta al mismo intent.
- UGC (reddit, facebook) o gobierno en el top-3 → hueco de **respuesta estructurada** que nadie da.
- Colisión de marca (otro "short load" en otro estado) → **navigational ajena**; no se persigue.

Escríbelo en el informe como *proxy* y cita la fila del SERP que lo sostiene.

## Clustering

- Mínimo 10 keywords para clusterizar; con menos, lista plana.
- Un cluster = una intención + una página destino. Dos clusters con la misma página → fusionar.
- **Canibalización**: en GSC (90 d) la misma query o el mismo intent aparece servido por ≥ 2 URLs
  propias → señalarlo con las dos URLs y cuál debería ganar (la de más impresiones, salvo que la
  otra sea la plantilla correcta).
- Striking distance (pos 5-30, ≥ 3 impresiones) manda sobre cualquier volumen: Google ya nos
  muestra ahí; el trabajo es subir 5 puestos, no crear.

## Pre-supply

Si `positioning` dice que no hay operador activo: las clases *near-me* y las T4 con precio
exacto **no** son priorizables (el sitio no puede cumplir la promesa). Se listan bajo
"preparado para cuando firme el primer operador".
