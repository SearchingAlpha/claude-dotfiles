---
description: Genera y carga en el contexto un mapa semántico de símbolos del repositorio (funciones, clases, exports) usando el patrón Repo Map de Aider.
argument-hint: [ruta_opcional_subdirectorio]
---

# /repomap — Mapa Semántico de Símbolos del Repositorio

Invocas el extractor de símbolos de `~/claude-dotfiles/harnesses/tools/repomap.py` para obtener una vista panorámica compacta del código sin saturar el contexto con ficheros enteros.

Ruta objetivo: `$ARGUMENTS` (o directorio actual si no se especifica).

## Instrucciones de ejecución

1. Ejecuta el extractor sobre el directorio objetivo:
   ```bash
   python ~/claude-dotfiles/harnesses/tools/repomap.py ${ARGUMENTS:-.}
   ```
2. Lee la salida generada y tenla presente como la **fuente de verdad de qué clases, métodos y funciones existen ya** en el proyecto.
3. Responde al usuario con:
   - Un resumen de 2 líneas sobre la arquitectura identificada (módulos principales y entidades).
   - Confirmación de que ya conoces los símbolos existentes para no duplicar código ni inventar imports.
