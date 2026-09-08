# Cursor Playbook — construir rápido sin quedarte atascado

Tú ya construyes con agentes (Cursor-style). Este doc no te enseña a usar Cursor,
te da el **protocolo para cuando el agente se atasca** — que es donde se pierden
las horas en un hackathon.

## Setup mínimo antes del evento

- Instala Cursor y logéate (crédito del sponsor).
- Abre esta carpeta como proyecto. Cursor lee el código y los comentarios como
  contexto, así que arrancas ya "en tema".
- Ten `.env` con las 3 claves y corre las 4 pruebas de humo (`python llm.py`, etc.).
  Si las 4 pasan, el día 1 no dependes de nada.

## Cómo pedirle cosas a Cursor (para que acierte a la primera)

1. **Dale el objetivo Y el ancla.** No "haz un chatbot", sino "añade una tool
   `get_weather` al agente de `agent.py` siguiendo el mismo patrón que `web_search`".
   Señala el archivo y la función de referencia.
2. **Un cambio por prompt.** Cambios pequeños = fáciles de revisar y de revertir.
3. **Pídele que ejecute y te enseñe el resultado**, no solo que escriba código.
4. **Cuando algo funcione, haz commit** (`git add -p` + commit). Es tu punto de
   restauración. En un hackathon el mayor riesgo no es no avanzar, es *romper lo
   que ya iba*.

## 🚨 Cuando el agente entra en bucle (el protocolo)

Señales: repite el mismo cambio, "arregla" A y rompe B, o el error no se mueve.
No sigas dándole al botón. Para y ejecuta esto **en orden**:

1. **PARA.** Un tercer reintento con el mismo prompt casi nunca funciona. Estás
   quemando tiempo y crédito.
2. **Lee el error de verdad tú mismo.** La última línea del traceback + el nombre
   del archivo y la línea. El 80% de las veces dice exactamente qué pasa
   (KeyError, clave de API ausente, timeout, campo que no existe).
3. **Reproduce lo mínimo.** Corre el módulo aislado (`python search.py`) para saber
   *qué capa* falla: ¿Claude, Exa, fal, o tu pegamento? No depures el sistema
   entero, depura la pieza.
4. **Reformula el prompt con el error dentro.** Pega el traceback literal y di
   "esto falla con este error, arréglalo". Un error concreto vale más que diez
   "no funciona".
5. **Si sigue en bucle: revierte al último commit que iba** (`git stash` o
   `git checkout .`) y prueba un camino distinto. Mejor 20 min perdidos que 2 horas.
6. **Baja de modelo para probar rápido.** Pon `CLAUDE_MODEL=claude-sonnet-5` para
   iterar veloz; vuelve a Opus para lo difícil.
7. **Última bala: pregunta a un humano.** Los mentores/organizadores están para
   esto. Pedir ayuda a los 30 min es más listo que a las 3 horas.

> Regla de oro: **el agente no sabe que está atascado; tú sí.** Tu trabajo en el
> equipo es exactamente ese — deep dive del fallo y decidir. Aquí es lo mismo.

## Anti-patrones que cuestan el hackathon

- Dejar al agente reescribir medio proyecto de una sola tirada (imposible de revisar).
- No hacer commits → cuando algo se rompe, no hay vuelta atrás.
- Depurar mirando el output final en vez del error real.
- Añadir features nuevas con la base rota. Primero verde, luego construir.
- Perseguir "que quede perfecto" el día 1. Primero que funcione de punta a punta.
