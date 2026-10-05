---
description: Abre el dashboard de shepherd (qué desarrolla cada sesión de Claude Code, en qué rama, su review y sus conflictos) y resume lo que pide acción. Solo lectura sobre las tracks de otras sesiones.
argument-hint: "[stop | doctor]"
---

# /shepherd — panel de las sesiones en paralelo

shepherd (`~/claude-dotfiles/harnesses/shepherd/`) registra cada sesión de Claude
Code como una *track*: su rama, su worktree, los paths que posee y la review
(PR) donde termina. Este comando enseña ese panel. **No** abre reviews, no
hace `finish` ni cambia el modo de protección de ningún repo.

## Qué hacer

1. Según `$1`:
   - vacío → arranca (o reutiliza) el servidor del dashboard en segundo plano:

     ```bash
     shepherd serve --daemon
     ```

     Enseña al usuario la URL **tal cual** (lleva el token de acceso) y la pista
     de reenvío de puerto que imprime (VS Code → Ports, o `ssh -L`).
   - `stop` → `shepherd serve --stop` y termina.
   - `doctor` → `shepherd doctor`, enseña la salida tal cual y explica en una
     línea cada ✗ (qué ejecutar para arreglarlo). Termina.

   Si `shepherd` no está en el PATH, usa
   `python3 ~/claude-dotfiles/harnesses/shepherd/shepherd.py` y sugiere ejecutar
   `~/claude-dotfiles/install.sh`.

2. Ejecuta `shepherd status --all` y presenta la salida **tal cual**.

3. Añade debajo un resumen de 2-3 líneas con lo accionable, por orden de
   urgencia:
   - ⚠ sesiones trabajando en la rama protegida o worktrees sin track,
   - conflictos previstos entre tracks (`↔ … predicted conflicts`),
   - tracks cuya sesión terminó (`owner session ended`) o sin dueño,
   - reviews aprobadas o ya mergeadas que esperan `shepherd finish`,
   - avisos de autenticación de los backends de review (p. ej. «run …»).

## No hagas

- No ejecutes `shepherd pr`, `finish`, `resume`, `sync` ni `pause` sobre tracks de
  otras sesiones: díselo al usuario y que decida.
- No ejecutes `shepherd init`, `mode` ni `uninit`: cambian la protección del repo
  y son solo del usuario.
- No edites nada dentro de `.git/shepherd/` ni `claude-sessions.md` (es una vista
  generada).
