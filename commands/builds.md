---
description: Muestra un dashboard de una ojeada de todos los builds autónomos (loop.py) en curso o parados — progreso, fase actual, escalaciones y último commit. Solo lectura, no arranca nada.
argument-hint: "[raíz opcional de builds, por defecto ~/builds]"
---

# /builds — panel de builds autónomos

Resume el estado de todos los proyectos construidos por el harness de
`~/claude-dotfiles/harnesses/autonomous-coding/`. Es **solo lectura**: no lanza
sesiones ni toca el estado de ningún build.

## Qué hacer

1. Ejecuta el script de dashboard (descubre los builds y los resume):

   ```bash
   python3 ~/claude-dotfiles/harnesses/autonomous-coding/build_status.py ${1:+--root "$1"}
   ```

   Si `$1` está vacío, escanea `~/builds` por defecto.

2. Presenta la tabla al usuario **tal cual la imprime el script** (ya viene con
   barras de progreso y emojis de estado). No la re-tabules a mano.

3. Añade debajo un resumen breve en 2-3 líneas que destaque lo accionable:
   - cuáles siguen 🟢 corriendo y cuáles están ⚪ parados,
   - si alguno tiene 🔴 escalaciones (bloqueos que requieren intervención humana —
     eso es lo más urgente; explica que hay que arreglar `contracts.json`/el código
     y resetear la fase en `state.json`),
   - si alguno acaba de terminar (100%).

4. Si el usuario pasó un proyecto concreto o quiere más detalle de uno, usa el
   `--status` del harness para ese build (tabla requisito a requisito):

   ```bash
   python3 ~/claude-dotfiles/harnesses/autonomous-coding/loop.py --project-dir ~/builds/<nombre> --status
   ```

## No hagas

- No arranques ni reinicies ningún `loop.py`.
- No modifiques `spec.json` / `contracts.json` / `state.json` de ningún build.
- No leas los `run.log` enteros salvo que el usuario pida diagnosticar un fallo
  concreto (son enormes); para el estado normal basta el script.
