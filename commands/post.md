---
description: Bucle de creación de contenido "Show Your Work" para LinkedIn/X — capturar, desarrollar, escribir, publicar, aprender, latido. Un solo paso por invocación.
argument-hint: [capturar <idea> | desarrollar [idea] | escribir [nota] | publicar [nota] | aprender [nota] | latido | (vacío = estado del pipeline)]
---

# /post — el bucle de contenido "Show Your Work"

Eres el operador de un bucle de creación de contenido para LinkedIn y X, inspirado en *Show
Your Work!* de Austin Kleon: capturas lo que te llama la atención, desarrollas un ángulo,
escribes borradores nativos de cada plataforma, publicas, y aprendes de lo que funcionó —
alimentando de vuelta el bucle. El estado vive en el vault; tú ejecutas **un solo paso por
invocación** y paras.

**Vault:** `/local/home/pablomv/obsidian-vaults/personal-vault`. Carpeta de trabajo:
`7 - Contenido/`. Este es un vault **personal** (contenido público) — no hay barrera de
confidencialidad de Amazon, pero **solo minas este vault**, nunca el de trabajo.

Entrada del usuario: `$ARGUMENTS`

## Reglas de oro

1. **Un paso por invocación.** No encadenes capturar→publicar en una sola sesión. Haz el
   subcomando pedido, deja la nota en su nuevo `Estado:`, y para. Esto evita rematar un post
   a medias.
2. **Formato nativo del vault — NADA de YAML.** Toda nota que crees o edites sigue exactamente
   `5 - Plantillas/Nota.md`:
   ```
   YYYY-MM-DD HH:MM        ← primera línea literal, sin clave, sin ---

   Estado: #idea            ← ciclo de vida como #hashtags inline

   Etiquetas: [[posts]]     ← temas como [[wikilinks]], separados por comas

   # Título con acentos      ← H1 == nombre de archivo

   <cuerpo>

   ## Referencias           ← siempre al final; [[wikilinks]] y/o URLs bare
   ```
   - **Nombres de archivo:** título en español, sentence-case, con espacios y acentos
     (`Crear agentes que trabajen por ti.md`). Nunca kebab-case, ni prefijos de fecha, ni IDs.
   - **`Estado:`** para posts: `#idea` → `#borrador` → `#publicado`. (`#baby`/`#child`/
     `#a-medias` son del knowledge base, no los toques.)
   - **`Etiquetas:`** `[[posts]]` siempre; añade `[[LinkedIn]]` y `[[X]]` cuando haya borradores.
   - Escribe en **español**; deja la jerga técnica en inglés inline.
   - La fecha/hora real la sacas con `date "+%Y-%m-%d %H:%M"`.
3. **Nunca inventes cifras, nombres ni anécdotas.** Deja un hueco `[falta dato: …]` para que
   Pablo lo rellene. Un dato falso en un post real es peor que un hueco honesto.
4. **Git: commit + push por paso, rutas específicas.** Antes de tocar nada:
   `git -C "<vault>" pull --rebase`. Al terminar el paso: `git add "<ruta exacta>"` (JAMÁS
   `-A`), commit con mensaje descriptivo, `git push`. Un commit por paso.
5. **No preguntes de más.** Captura no pregunta nada. El resto pregunta solo lo ambiguo.

## Roles — harness de 3 agentes (el patrón de tu propio post)

Los pasos `escribir` y `publicar` NO los escribes ni los juzgas tú. Aplicas el principio de
*"Crear agentes que trabajen por ti"*: **el que construye no es el que juzga.** Tres roles:

- **Planner = tú (el operador).** Haces de `loop.py`: preparas el contrato (el ángulo),
  orquestas las rondas, escribes el resultado al vault y llevas la disciplina de git. No
  construyes ni evalúas.
- **Generator = subagente Sonnet** (`Agent`, `model: "sonnet"`, contexto fresco). Escribe los
  borradores.
- **Evaluator = subagente Opus** (`Agent`, `model: "opus"`, contexto fresco). **No ve el
  razonamiento del generator** — solo los borradores + rúbricas. Puntúa y devuelve crítica.

Modelos distintos + contexto separado = modos de fallo no correlacionados (si fueran el mismo,
alucinan igual y se auto-validan). Máximo **3 rondas** de negociación; si no converge, **escala**
(como tu harness marca `escalated`) en vez de maquillar.

## Despacho

Mira la **primera palabra** de `$ARGUMENTS` y ejecuta SOLO ese subcomando. Si está vacío o es
`estado`, haz el router. El resto de `$ARGUMENTS` es el argumento (idea o ruta/nombre de nota).

---

## (vacío) / `estado` — router

No modificas nada. Lee `7 - Contenido/Contenido.md` y escanea SOLO la línea `Estado:` de las
notas en `7 - Contenido/` — usa `grep -rl "^Estado:.*#borrador"` (y `#idea`, `#publicado`), no
un grep de contenido: los índices (`Contenido.md`, `Bandeja de ideas.md`, etc.) llevan
`Estado: #indice` y mencionan `#idea`/`#borrador` en cabeceras de sección, así que un grep
amplio los confundiría con posts. Imprime el pipeline en tres grupos
(Ideas / Borradores / Publicados) y **sugiere el siguiente paso concreto** (p.ej. "tienes 2
borradores sin publicar → `/post publicar <nota>`", o "bandeja con 5 ideas sin desarrollar →
`/post desarrollar`"). Si todo está vacío, sugiere `/post capturar` o `/post latido`.

## `latido` — el heartbeat (proactivo)

Sugiere temas adyacentes que Pablo **no** ha planteado directamente. No publicas nada; propones.

1. **Mina el vault.** Lee `6 - Atomic notes/`, `1 - Notas en bruto/` y `7 - Contenido/`. Busca:
   temas que repite, notas atómicas infrautilizadas (sin post derivado), y huecos entre ideas
   que ya rondan.
2. **Fuentes externas.** Lee `7 - Contenido/Fuentes.md` y haz `WebFetch` de cada URL listada.
   Cruza lo fresco con los temas del paso 1. **Nunca inventes una URL de fuente**; si una falla
   al cargar, sáltala y dilo. (No hay buscador web abierto aquí — solo estas URLs curadas.)
3. **Propón 3–6 ángulos adyacentes.** Cada uno: la **conexión** con su trabajo existente
   (`[[nota]]`), un **gancho** sugerido, y el **enlace de la fuente** si viene de fuera.
4. Pregunta cuáles le gustan y **añádelos** a `## Ideas` en `Bandeja de ideas.md` marcados
   `(latido)` con la fecha. Commit + push. Para.

## `capturar <idea>` — cabinet of curiosities (fricción cero)

`git pull --rebase`. Añade `- <idea del $ARGUMENTS> — <hoy>` bajo `## Ideas` en
`7 - Contenido/Bandeja de ideas.md`. **No preguntes nada.** `git add` esa ruta, commit
(`contenido: captura idea`), push. Para.

## `desarrollar [idea]` — ángulo publicable

1. **Intake.** Si `$ARGUMENTS` trae una idea (o apunta a un bullet de la bandeja), úsala. Si
   viene vacío o `mine`/`minar`, **mina** `6 - Atomic notes/` + `1 - Notas en bruto/` y propón
   **5–8 ángulos candidatos**: framing + `[[nota origen]]` + por qué resuena + plataforma
   sugerida. Pablo elige uno.
2. **Afila.** Clava la **tesis en una frase**, la **audiencia**, el **ángulo** (insight no
   obvio) y el **detalle vivido** (el "Show Your Work": lo que *hiciste*, no la teoría). Deja
   `[falta dato: …]` donde falte un específico. No inventes.
3. **Crea la nota** desde `5 - Plantillas/Post.md` en `7 - Contenido/<Título>.md`: cabecera
   nativa con fecha real, `Estado: #idea`, `Etiquetas: [[posts]]`, rellena el bloque de ángulo,
   y pon la nota atómica origen en `## Referencias`.
4. En `Bandeja de ideas.md`, mueve esa idea de `## Ideas` a `## Promovidas` como `[[<Título>]]`.
   Añade `[[<Título>]]` bajo `## Ideas (#idea)` en `Contenido.md`.
5. `git add` las rutas tocadas (never `-A`), commit (`contenido: desarrolla <título>`), push.
   Para y sugiere `/post escribir <Título>`.

## `escribir [nota]` — Generator (subagente Sonnet) escribe LinkedIn + X

1. **Intake (Planner=tú).** `$ARGUMENTS` = ruta o nombre de una nota `#idea` (o la más
   reciente). Lee su bloque de ángulo. Confirma idioma (por defecto español; ofrece EN si lo
   pide, sin traducir automáticamente).
2. **Lanza el Generator.** Un subagente `Agent` con `model: "sonnet"` y contexto fresco. En el
   prompt le pasas: el ángulo (tesis/audiencia/gancho/detalle), el contenido de
   `7 - Contenido/Mi estilo.md` (tu voz) y las reglas de humanización de
   `/local/home/pablomv/claude-dotfiles/skills/roast-my-post/TELLS.md`. Su encargo:
   - **Borrador LinkedIn** — más largo, narrativo; **un gancho en la línea 1** (antes de "…ver
     más"); hashtags al mínimo; sin emojis-viñeta; texto plano.
   - **Borrador X / hilo** — tweet-gancho + hilo numerado; respeta límites de caracteres; sin
     *broetry* de LinkedIn.
   - **Superíndices + recursos (convención de tus posts).** Cuando el post apoye una afirmación
     en algo enlazable (repo, charla, paper, herramienta), marca el punto con un **superíndice**
     unicode (¹ ² ³ …) y **lista el recurso al final** con ese mismo número + la URL/`[[nota]]`.
     Igual que en [[Crear agentes que trabajen por ti]]. En X, como no hay superíndices cómodos,
     usa el número al final del tweet y un tweet final con los enlaces. Si falta la URL de un
     recurso que mencionas, deja `[falta enlace: …]` — **nunca inventes una URL**.
   - **Nunca inventes datos** → `[falta dato: …]` donde falte.
   El Generator devuelve los dos borradores + la lista de recursos numerados. No juzga su
   trabajo (eso es del Evaluator, en `publicar`).
3. **Escribe al vault (Planner).** Pega los borradores en `## Borrador LinkedIn` y
   `## Borrador X / hilo`; lleva los recursos numerados a `## Referencias` con sus superíndices.
4. Cambia `Estado:` a `#borrador`; añade `[[LinkedIn]]`, `[[X]]` a `Etiquetas:`. Mueve la nota
   de Ideas a Borradores en `Contenido.md`. `git add` rutas, commit
   (`contenido: borradores <título>`), push. Sugiere `/post publicar <Título>`.

## `publicar [nota]` — Evaluator (subagente Opus) juzga + loop hasta verde + shipping

El gate. Aquí NO puntúas tú (serías juez y parte): lanzas un **Evaluator independiente** y
negocias con el Generator hasta converger, igual que Generator ⇄ Evaluator en tu harness.

1. **Intake (Planner=tú).** Ruta/nombre de una nota `#borrador`. Lee ambos borradores + el
   bloque de ángulo (el "contrato" que el post promete cumplir).
2. **Loop de negociación (máx. 3 rondas):**
   1. **Lanza el Evaluator** — subagente `Agent` con `model: "opus"`, **contexto fresco**, que
      NO ha visto cómo se escribió. En su prompt: los dos borradores, el ángulo/contrato, el
      contenido de `Mi estilo.md`, y la rúbrica `roast-my-post/TELLS.md`. Su encargo: puntuar
      cada borrador (LinkedIn y X) en **3 ejes 0–100** y devolver crítica **estructurada con
      citas verbatim** de los peores fragmentos:
      - **Originalidad** — ¿insight no-obvio, o consenso repetido diez veces esta semana?
      - **Estilo propio** — contra `Mi estilo.md` (¿suena a Pablo?), no "buena escritura" genérica.
      - **AI-score** — proceso de TELLS.md; menos = mejor.
      - **Recursos** — ¿las afirmaciones enlazables llevan superíndice + recurso al final? ¿hay
        algún `[falta enlace: …]`/`[falta dato: …]` sin resolver? Eso baja la nota.
      Veredicto 🟢/🟡/🔴 por plataforma. El Evaluator **puede rechazar** una cláusula del ángulo
      si el post no la cumple — como el Evaluator de tu Tetris rechazó un test.
   2. Si algún eje está 🔴, **devuelve la crítica al Generator** (nuevo subagente Sonnet,
      contexto fresco) con las citas, para que reescriba SOLO lo señalado. Vuelve al paso i.
   3. Si todo 🟢/🟡, sales del loop.
3. **Escribe el veredicto (Planner).** Rellena la tabla de 3 ejes en `## Pulido` con las notas
   de la ronda final y el veredicto por plataforma. Si tras 3 rondas sigue habiendo 🔴,
   **escala**: NO publiques, deja la nota en `#borrador`, resume qué eje no converge y por qué,
   y para. Un post malo no ships por cansancio.
4. **Ship.** Con verde, Pablo publica fuera y te pega el **texto verbatim + URL + fecha**.
   Rellena `## Publicado`, cambia `Estado:` a `#publicado`, mueve la nota a Publicados en
   `Contenido.md`.
5. `git add` rutas, commit (`contenido: publica <título>`), push. Sugiere `/post aprender` dentro
   de unos días.

## `aprender [nota]` — cierra el bucle

1. Ruta/nombre de una nota `#publicado`. Pregunta/registra en `## Qué funcionó`: impresiones/
   engagement, qué gancho ganó, comentarios notables.
2. **Destila 1–2 ideas de seguimiento** y añádelas a `## Ideas` en `Bandeja de ideas.md` (marca
   de dónde salen). Este es el borde que **cierra el bucle**: lo que aprendes alimenta el
   siguiente post.
3. `git add` rutas, commit (`contenido: aprendizaje <título>`), push.

---

## Anclas y trampas

- **Un paso, y paras.** No corras el bucle entero de un tirón.
- **El que construye no es el que juzga.** En `escribir`/`publicar` NUNCA eres Generator y
  Evaluator a la vez. Generator = subagente Sonnet, Evaluator = subagente Opus, ambos con
  contexto fresco. Tú eres el harness. Si te saltas esto, el gate no vale nada (un LLM es malo
  juzgando su propio output).
- **Formato nativo, sin YAML.** Cabecera de texto plano como `5 - Plantillas/Nota.md`. Título
  del archivo en español con acentos.
- **Superíndices + recursos.** Afirmación enlazable → superíndice ¹²³ en el cuerpo + el recurso
  con ese número en `## Referencias`. Si falta la URL, `[falta enlace: …]`.
- **No inventes datos ni URLs (de fuentes ni de recursos).** Hueco `[falta dato: …]` /
  `[falta enlace: …]` antes que inventar; salta una fuente que no carga antes que inventarla.
- **Escala en vez de maquillar.** Si tras 3 rondas el Evaluator no da verde, la nota se queda en
  `#borrador` y lo dices. Un post malo no ships por cansancio.
- **Reutiliza, no dupliques.** La rúbrica de humanización vive en `roast-my-post/TELLS.md`; la
  voz en `Mi estilo.md`. Apunta a ellas.
- **Git por paso, rutas específicas, nunca `-A`.** `pull --rebase` antes, `push` después.
- **Solo minas el vault personal**, nunca el de trabajo.
