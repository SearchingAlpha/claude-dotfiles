---
description: Lanza el harness autónomo de 3 agentes (Planner/Generator/Evaluator) para construir una app completa sin supervisión. Prepara app_spec.txt, arranca loop.py en segundo plano y te da los comandos para monitorizar e intervenir.
argument-hint: [descripción de la app a construir, o ruta a un app_spec existente]
---

# /autobuild — arranca una construcción autónoma real

Eres el operador que **prepara y lanza** el harness de long-running agents que vive en
`~/claude-dotfiles/harnesses/autonomous-coding/`. Tú NO construyes la app dentro de esta
sesión: la app la construye `loop.py`, que corre como proceso externo durante horas y
lanza decenas de sesiones `claude -p` (Planner → Generator ⇄ Evaluator → build → verdict).
Tu trabajo es dejar todo listo, arrancarlo desacoplado del terminal, y entregar al usuario
los comandos para vigilarlo e intervenir.

Entrada del usuario (opcional): `$ARGUMENTS`

## Reglas

1. **No construyas la app tú.** Si te dan ganas de "empezar a picar código", párate: eso
   es trabajo del harness. Tu único output son artefactos de arranque + instrucciones.
2. **No inventes el `app_spec.txt`.** Es la única fuente de intención. Si la descripción del
   usuario es vaga, haz 2-3 preguntas de aclaración ANTES de escribirlo (stack preferido si
   lo tiene, alcance mínimo vs. completo, cualquier must-have). No sobre-especifiques: el
   Planner ya descompone en requisitos.
3. **Confirma antes de lanzar.** Enseña el `app_spec.txt` y el comando exacto, y pide OK.
   Lanzar consume tokens de verdad durante horas.
4. **Sé honesto sobre el coste.** Recuérdale que una app completa son muchas sesiones de
   contexto completo (4+ por requisito). No es barato.

## Paso 1 — Localiza el harness

Resuelve la ruta del harness (normalmente `~/claude-dotfiles/harnesses/autonomous-coding`).
Verifica que existe `loop.py`. Si no, dilo y detente.

## Paso 2 — Preflight del entorno

Comprueba (solo lectura, no instales nada sin permiso):
- `claude --version` — CLI en PATH.
- Chromium de Playwright disponible: `ls -d ~/.cache/ms-playwright/chromium* 2>/dev/null`.
  Si falta, dile al usuario que ejecute `npx playwright install chromium` (una vez) y
  ofrécele hacerlo tú si lo autoriza.

## Paso 3 — Prepara el proyecto y el app_spec.txt

- Elige/confirma un `--project-dir` FUERA del repo de dotfiles (p.ej. `~/builds/<nombre>`).
  Nunca dentro de `~/claude-dotfiles`.
- Si `$ARGUMENTS` es una ruta a un `app_spec.txt` existente, úsalo tal cual.
- Si es una descripción (o está vacío), redacta un `app_spec.txt` claro y conciso a partir
  de la descripción del usuario (tras aclarar dudas). Formato: prosa breve + bullets de
  comportamientos observables por el usuario + restricciones (stack, sin auth, etc.).
- Crea el dir y escribe `app_spec.txt`. Enséñaselo al usuario.

## Paso 4 — Confirma y lanza (desacoplado)

Muestra el comando exacto y pide confirmación. Al lanzar, usa `setsid` + redirección a un
log para que **sobreviva al cierre del terminal** y no bloquee esta sesión:

```bash
cd ~/claude-dotfiles/harnesses/autonomous-coding
setsid python3 loop.py --project-dir <PROJECT_DIR> \
  --notify-email <TU_EMAIL> --notify-every-pass \
  > <PROJECT_DIR>/run.log 2>&1 < /dev/null &
echo "PID $! — corriendo. Log: <PROJECT_DIR>/run.log"
```

Sin `--max-iterations`, `--only` ni `--review-every`: **run real, sin límites**. (Si el
usuario prefiere una primera pasada con freno, ofrécele `--review-every 5` o
`--max-iterations N`.)

### Notificaciones por email (activadas por defecto arriba)

- `--notify-email` + `--notify-every-pass` → un correo por cada requisito que pasa,
  más los eventos terminales (fin, escalación, error fatal). Si el usuario prefiere
  menos ruido, quita `--notify-every-pass` (solo avisa en eventos terminales).
- **Prerrequisito — credenciales Midway.** El envío usa Amazon SES, que necesita
  credenciales AWS vigentes. Si han caducado, el harness registra
  `email notification failed (CredentialRetrievalError … run mwinit)` y **sigue
  construyendo sin avisar**. Antes de lanzar, recuérdale al usuario correr `mwinit`
  (interactivo — que lo teclee él con `! mwinit`) si lleva horas sin autenticarse.
- El `<TU_EMAIL>` debe ser una identidad verificada en SES (sandbox: remitente ==
  destinatario). Sustitúyelo por el correo del usuario al lanzar.
- Comprobación opcional de que el correo saldrá (no envía nada de más):
  `aws ses get-identity-verification-attributes --identities <TU_EMAIL> --region us-east-1`
  debe devolver `VerificationStatus: Success`.

## Paso 5 — Entrega los comandos de monitorización e intervención

Imprime este bloque, sustituyendo `<PROJECT_DIR>`:

```bash
# Ver progreso (solo lectura, no lanza nada):
python3 ~/claude-dotfiles/harnesses/autonomous-coding/loop.py --project-dir <PROJECT_DIR> --status

# Ver el stream en vivo:
tail -f <PROJECT_DIR>/run.log

# Historial de commits (uno por requisito superado):
git -C <PROJECT_DIR> log --oneline

# Pausar: Ctrl+C si está en foreground, o `kill <PID>`. TODO el estado está en disco.
# Reanudar: repite EXACTAMENTE el mismo comando de lanzamiento — sigue por la siguiente fase pendiente.
```

Explica brevemente:
- **Resumible:** cada sesión arranca con contexto fresco; el estado vive en
  `spec.json` / `contracts.json` / `evaluations.json` / `state.json` + git. Matar y relanzar
  el mismo comando reanuda sin perder nada.
- **Escalación:** si un requisito agota las rondas de negociación o los reintentos de build,
  el run se detiene y marca ese requisito como `escalated`. Hay que arreglar
  `contracts.json`/`state.json` a mano (o el código) y relanzar. Con `--status` ves cuál.
- **App terminada:** cuando `--status` muestre todos los requisitos en `done`, arranca la app
  con `cd <PROJECT_DIR> && ./init.sh`.

## Recordatorio final

No te quedes vigilando el proceso en esta sesión (correría horas). Lanza, entrega los
comandos, y devuelve el control al usuario. Si te pide después "¿cómo va?", ejecuta
`--status` y resume el resultado.
