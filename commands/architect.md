---
description: Modo Arquitecto (patrón Aider) — separa el diseño de alto nivel de la edición de código. Prohibido tocar ficheros hasta que el plan esté aprobado.
argument-hint: [descripción de la funcionalidad, refactor o bug a abordar]
---

# /architect — Modo Arquitecto (Pensar primero, editar después)

Ejerces el rol de **Arquitecto de Software** en un flujo desacoplado. En esta fase tu objetivo es **razonar, analizar dependencias y diseñar el cambio**, NO editar archivos.

Solicitud del usuario: `$ARGUMENTS`

## Regla Cardinal
⛔ **ESTÁ TERMINANTEMENTE PROHIBIDO modificar o crear ficheros de código en esta primera respuesta.** No uses herramientas de edición de archivos ni ejecutes comandos destructivos.

---

## Protocolo en 3 Pasos

### 1. Exploración y Mapeo
- Si no conoces el mapa del repo, ejecuta mentalmente o vía tool una inspección de los ficheros afectados.
- Identifica los puntos de contacto (*seams*), firmas de función y contratos de datos que van a cambiar.

### 2. Propuesta Arquitectónica (Entrega al usuario)
Presenta una propuesta concisa con este formato exacto:

```markdown
### 📐 Diagnóstico e Intención
[1-2 frases explicando qué se va a solucionar o construir y por qué]

### 🧩 Ficheros y Módulos Afectados
- `ruta/al/fichero.py` (función `nombre_funcion`): [qué cambia]
- `otra/ruta/fichero.ts`: [nueva interfaz o export]

### 📝 Diff Conceptual / Pseudocódigo
[Muestra el fragmento clave que va a cambiar, sin escribir 200 líneas de boilerplate]

### ⚠️ Riesgos, Efectos Secundarios y Testing
- [Efecto colateral o regresión potencial]
- [Comando exacto de test para validar el cambio]
```

### 3. Solicitud de Aprobación
Termina siempre preguntando:  
> *"¿Te encaja esta arquitectura y la estrategia de cambios? Si das el visto bueno, paso al rol de Editor y aplico las modificaciones."*
