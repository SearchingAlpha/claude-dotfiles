# TELLS — la rúbrica

Los "tells" son las huellas que deja el texto generado por IA. Esta es la lista contra la que
puntúas y roasteas. Bilingüe ES + EN.

## Principio rector (léelo antes de puntuar)

**Los tells son probabilísticos, no una prueba.** Un humano puede usar un guion largo, una
metáfora o una regla de tres sin ser una IA. Lo que delata a la IA es la **densidad** y la
**co-ocurrencia**: muchos tells juntos, en un texto uniforme y sin nada concreto vivido. Por eso:

- **Normaliza por longitud.** Cuenta tells por cada 100 palabras, no en absoluto. Tres clichés en
  un post de 80 palabras es grave; en uno de 400, menos.
- **Tres niveles de peso:**
  - **Hard** — casi nunca lo escribe un humano de forma natural. Cada aparición pesa fuerte.
  - **Soft** — sospechoso solo si **2+ co-ocurren**. Una aparición aislada no cuenta.
  - **Closers** — cierres de relleno; en LinkedIn pesan más porque nadie habla así.
- **Un solo tell no condena.** El veredicto sale del conjunto, no de una línea.
- **Lo semántico manda sobre lo léxico.** La *ausencia de concreto vivido* (§4) es el tell más
  fuerte y el más difícil de fingir. Un texto sin ningún cliché pero sin un solo nombre, número,
  fecha o anécdota sigue oliendo a IA.

---

## 1. Léxicos — palabras y frases

### EN

**Hard blacklist** (cada aparición pesa): *delve, tapestry, a testament to, in today's fast-paced
world, in today's digital age, navigate the (complex) landscape, unlock the potential/power of,
a game-changer, it's worth noting that, ever-evolving.*

**Soft** (flag si 2+ co-ocurren): *leverage, robust, landscape, realm, foster, harness, seamless,
meticulous, pivotal, crucial, underscore, showcase, myriad, plethora, elevate, resonate, holistic,
nuanced, intricate, boasts, vibrant, streamline, empower.*

**Closers de relleno**: *in conclusion, in summary, ultimately (como arranque de párrafo), the
bottom line is, at the end of the day, let that sink in, the future is here, only time will tell.*

### ES

**Hard blacklist**: *en el vertiginoso/trepidante mundo de, en la era (digital / de la IA), no es
solo X, es Y (ver §2), desbloquear el potencial, un antes y un después, cabe destacar que, en
constante evolución, el poder de.*

**Soft** (flag si 2+ co-ocurren): *potenciar, aprovechar el poder de, sin fisuras, robusto, clave
para, sumérgete, abrazar (el cambio / la incertidumbre), impulsar, fomentar, perfecto/a para,
integral, matizado, revolucionar, allanar el camino, en definitiva, sólido, ecosistema (metafórico).*

**Closers de relleno**: *en resumen, en conclusión, en última instancia, en definitiva, la
conclusión es clara, a fin de cuentas, que esto cale, el futuro ya está aquí.*

> Ojo con los falsos positivos: "clave", "robusto" o "leverage" pueden ser vocabulario técnico
> legítimo según el tema. Por eso son *soft* — solo cuentan en racimo.

---

## 2. Sintácticos y retóricos

- **Paralelismo negativo (la firma nº1).** Regexable: `no es solo … (,|—|:) es …` / `not (just|
  only|merely|simply) … (,|—|-) it'?s …` / "no va de X, va de Y" / "esto no es X. Es Y." /
  "not only … but also". Es el tell más reconocible de todos. Peso **hard**.
- **Regla de tres en piloto automático.** Tríos de adjetivos o cláusulas ("claro, conciso y
  convincente" / "más rápido, más inteligente y más eficiente") y listas de tres como *ritmo por
  defecto*. Una vez es retórica; tres veces en el mismo texto es IA.
- **Evitar el verbo "ser" con verbos de grandilocuencia.** "sirve como", "se erige como", "actúa
  como testimonio de", "representa un", "constituye" — donde un humano diría "es".
- **Preguntas retóricas de transición.** "¿Y esto qué significa?", "¿El resultado?", "But what
  does this really mean?", "So why does it matter?" como pivote de una línea.
- **Cláusulas de análisis con gerundio al final.** "…, destacando la importancia de…", "…,
  reflejando un cambio hacia…", "…, allanando el camino para…", "…, highlighting the shift
  toward…". Padding pseudo-analítico.
- **Densidad de hedging.** "puede", "podría", "en cierto modo", "generalmente", "a menudo",
  "algunos dirían" apilados → equilibrio sin compromiso.
- **Atribución comodín (weasel).** "los expertos coinciden", "estudios demuestran", "se dice que",
  "experts argue", "studies show" sin fuente nombrada.
- **Framing prefabricado.** "Y aquí está lo interesante:", "La cosa es así:", "Déjame ser
  sincero:", "Here's the thing:", "Let me be honest:" seguido de una lección genérica.
- **Variación elegante.** Llamar a lo mismo "la herramienta", "la solución", "la plataforma", "la
  propuesta" en frases seguidas para no repetir el sustantivo.

---

## 3. Estructurales y de formato

- **Abuso del guion largo (—).** Cuéntalos por 100 palabras. En un post casual de LinkedIn, la
  mera *presencia* de guiones largos tipográficos reales (—, no "-") es ya un tell leve: casi
  nadie los teclea a mano. Muchos y "bien puestos" para incisos = IA.
- **Comillas y apóstrofos tipográficos** (" " ' ') en una caja de texto que normalmente produce
  comillas rectas.
- **Emojis como viñetas** (✅ 🚀 💡 🔑 📌 ✨), uno por línea. Señal fortísima de slop de LinkedIn.
- **Negrita excesiva** — poner en **negrita** el arranque de cada ítem o esparcir énfasis cada
  pocas líneas.
- **Títulos en Mayúscula Inicial y secciones enlatadas** — "Ideas Clave", "Reflexiones Finales",
  "Key Takeaways", "Final Thoughts", "Challenges", "The Bottom Line".
- **Ritmo uniforme / "broetry".** Párrafos de largo casi idéntico; frases de largo casi idéntico;
  la pila de frases-de-una-línea buscando falsa contundencia. (Es baja *burstiness*, ver §5.)
- **Markdown filtrado** en una plataforma de texto plano: `**texto**`, `#`, `- ` pegados en
  LinkedIn (que no renderiza markdown).
- **Esqueleto formulaico.** gancho-pregunta → 3 viñetas → cierre inspirador → CTA ("¿Tú qué
  opinas? 👇"). El esqueleto-cliché del post de LinkedIn hecho por IA.

---

## 4. Tonales

- **Puffery / registro promocional.** "vibrante", "innovador", "de vanguardia", "revolucionario",
  "compromiso con la excelencia", "groundbreaking", "cutting-edge". Todo es superlativo.
- **Neutralidad sobre-equilibrada y aséptica.** Presenta los dos lados, no ofende a nadie, no moja.
  El movimiento "A pesar de los desafíos, X sigue…" / "Despite its challenges, X continues to…".
- **Ausencia de concreto vivido (el tell más fuerte).** Ni nombres, ni fechas, ni cifras, ni
  diálogo, ni una anécdota específica. Sustantivos genéricos ("un proyecto reciente", "un
  cliente", "muchos profesionales") en vez de "el martes que se cayó el WMS a las 3am". Los posts
  humanos se anclan en lo específico.
- **Inspiración forzada / falsa vulnerabilidad.** "Nunca olvidaré el día que…", "Déjame ser
  honesto…" seguido de moraleja genérica; cierres motivacionales ("Sigue adelante", "Tú puedes").
- **Explicar lo obvio.** Reformular la premisa como si fuera profunda; sobre-señalizar ("Primero,
  entendamos qué es X").
- **Afecto plano y sin arrugas.** Ni digresión, ni opinión de verdad, ni humor que arriesgue, ni
  un tangente desordenado. La escritura humana tiene textura y asimetría.

---

## 5. Cómo puntúan los detectores (intuición, no herramienta)

No corres ningún modelo. Aplicas *por criterio* la misma intuición en la que se basan los
detectores, porque explica por qué un texto "suena a IA":

- **Perplexity** — cómo de predecible es el texto para un modelo de lenguaje. Baja perplexity =
  el modelo habría elegido esas mismas palabras = probablemente IA. Traducción práctica: si cada
  palabra es la "esperable", huele a IA.
- **Burstiness** — la *varianza* del ritmo a lo largo del texto. Los humanos varían (frases cortas
  y largas mezcladas, ráfagas); la IA es uniforme. **Proxy barato: la desviación del largo de
  frase.** Frases todas de largo parecido = baja burstiness = tell.
- **GLTR** — la IA sobre-elige tokens de alto rango (los más probables). El texto humano mete
  sorpresas de bajo rango. Intuición: la falta total de palabras inesperadas es sospechosa.
- **DetectGPT** — el texto máquina se sienta en máximos locales de probabilidad; perturbarlo baja
  la probabilidad más que en texto humano. (Contexto, no lo aplicas a mano.)

Usa esto sobre todo para el subscore de §3 (ritmo uniforme) y como argumento en el roast:
"tus 6 frases miden 14–17 palabras cada una; eso es ritmo de plantilla, no de persona".

---

## 6. Scoring — de tells a 0–100

Puntúa cada categoría 0–100 (0 = nada de eso, 100 = saturado), luego pondera:

| Categoría | Peso | Qué mide |
|---|---|---|
| §4 Tonales (sobre todo falta de concreto) | **35%** | el tell semántico más fuerte |
| §2 Sintácticos/retóricos | 25% | paralelismo negativo, regla de tres, etc. |
| §1 Léxicos | 20% | blacklist (hard pesa; soft solo en racimo) |
| §3 Estructurales/formato | 20% | guiones, emoji-viñetas, ritmo uniforme, esqueleto |

`AI-score = 0.35·tonal + 0.25·sintáctico + 0.20·léxico + 0.20·estructural`

### Bandas de veredicto

| Score | Banda | Lectura |
|---|---|---|
| **0–20** | 🟢 **Humano** | Suena a persona. Textura, opinión, concreto. Díselo — no fuerces problemas. |
| **21–45** | 🟡 **Retoques menores** | Bien en el fondo; un par de clichés o un cierre de relleno que cortar. |
| **46–70** | 🟠 **Huele a IA** | Varios tells juntos y poco concreto. Necesita reescritura, no parche. |
| **71–100** | 🔴 **Slop puro** | Plantilla de IA de manual. Reescribir de cero conservando solo la idea. |

**Calibración anti-falso-positivo:** un texto seco, con opinión y con un dato/nombre concreto debe
caer en 🟢 aunque tenga un guion largo o una regla de tres. No inventes tells para justificar una
nota alta. Un humano brusco no es una IA.

---

## Fuentes

- Wikipedia, "Signs of AI writing" — https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing
  (semilla curada de los tells léxicos, retóricos, de formato y tonales; es una checklist basada
  en reglas)
- GPTZero, "Perplexity and Burstiness: What Is It?" — https://gptzero.me/news/perplexity-and-burstiness-what-is-it/
- GLTR — Gehrmann, Strobelt, Rush, ACL 2019 — https://arxiv.org/abs/1906.04043
- DetectGPT — Mitchell et al., ICML 2023 — https://arxiv.org/abs/2301.11305
