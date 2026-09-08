# HackSpain Starter Kit

Esqueleto para llegar al hackathon con la curva de aprendizaje ya hecha en las
tres herramientas sponsor: **Claude** (LLM/razonamiento), **Exa** (búsqueda/RAG)
y **fal.ai** (generación de imágenes). Todo en Python, pensado para escribir
encima con **Cursor**.

La idea: el día 1 no pierdes horas peleándote con SDKs ni con boilerplate. Pones
tus claves, verificas que las tres herramientas responden, y a construir tu idea.

## Qué hay aquí

```
hackspain-starter/
├── config.py        # lee .env; proveedor de LLM, modelo, defaults
├── llm.py           # Claude: chat() + run_agent() (bucle de tools a mano, legible)
├── search.py        # Exa: web_search()
├── media.py         # fal.ai: generate_image()
├── agent.py         # ata los 3: ask() (agente autónomo) + rag_answer() (RAG citado)
├── app.py           # demo web Streamlit (4 pestañas)
├── requirements.txt
├── .env.example     # copia a .env y rellena claves
└── playbooks/                  # kit de preparación (en inglés) — empieza por playbooks/README.md
    ├── 01-game-plan.md         # objetivos, tu rol, timeline 36h con checkpoints, CLI hackspain, submission
    ├── 02-challenge-framing.md # horas 1–3: del reto al Framing Brief
    ├── 03-tracks-dossier.md    # una sección por startup del track
    ├── 04-pitch-and-demo.md    # pitch 3 min para VCs + jueces de startup, demo, Q&A de 4 pasos
    ├── 05-networking.md        # antes / durante / después, lista de personas, ask ladder
    ├── me.md                   # intros de 20s, historia, banco de respuestas (personal)
    ├── contacts.md             # lo rellena /hs-contact
    └── cheatsheets/            # A4 imprimibles: kickoff-60, team-brief, pitch-card, people-card
```

## Arranque (5 minutos)

```bash
cd hackspain-starter
python -m venv .venv && source .venv/bin/activate     # opcional pero recomendado
pip install -r requirements.txt
cp .env.example .env          # y rellena ANTHROPIC_API_KEY, EXA_API_KEY, FAL_KEY
```

Verifica cada herramienta por separado (cada módulo tiene una prueba de humo):

```bash
python llm.py       # debe imprimir una frase de Claude
python search.py    # debe imprimir 3 resultados de búsqueda
python media.py     # debe imprimir una URL de imagen
python agent.py     # agente + RAG de punta a punta
```

Y la demo web:

```bash
streamlit run app.py
```

## Claves (dónde conseguirlas)

| Herramienta | Env var | Nota |
|---|---|---|
| Claude | `ANTHROPIC_API_KEY` | En el hackathon probablemente den crédito. Si usas AWS/Bedrock, mira abajo. |
| Exa | `EXA_API_KEY` | dashboard de Exa |
| fal.ai | `FAL_KEY` | dashboard de fal |

**Proveedor de Claude** (`LLM_PROVIDER` en `.env`):
- `anthropic` (por defecto) — API oficial, solo `ANTHROPIC_API_KEY`.
- `bedrock` — Amazon Bedrock, como en tu NarrativeAgent (`pip install "anthropic[bedrock]"`, credenciales AWS).
- `aws` — Claude Platform on AWS (`pip install "anthropic[aws]"`, `AWS_REGION` + `ANTHROPIC_AWS_WORKSPACE_ID`).

## Los dos patrones que cubren casi todo

1. **Agente con tools** (`agent.ask`): Claude decide solo cuándo buscar en la web
   o generar una imagen. El bucle está en `llm.run_agent` — escrito a mano y
   comentado para que puedas verlo y depurarlo (con `verbose=True` imprime cada
   llamada a tool). Añadir una tool nueva = añadir una entrada a `TOOLS` y un caso
   en `dispatch()`.
2. **RAG** (`agent.rag_answer`): recuperar con Exa + responder citando con Claude.
   Más fiable que dejar al modelo "recordar" hechos, y las citas dan credibilidad.

## Notas

- Modelo por defecto `claude-opus-5`. Para iterar más rápido/barato durante el
  hackathon, pon `CLAUDE_MODEL=claude-sonnet-5` en `.env` (no toques código).
- `flux/schnell` es la opción rápida y barata de imagen; sube a `fal-ai/flux/dev`
  si quieres más calidad para la demo final.
- **Nunca subas `.env`** — ya está en `.gitignore`. Las claves van solo en local.

> Este kit vive dentro de `claude-dotfiles`, un repo **privado** desde el 8 de
> septiembre de 2026 porque `playbooks/` contiene material personal. El
> `.gitignore` de esta carpeta excluye `.env` y las imágenes generadas igualmente.
> Los slash commands `/hs-*` que acompañan al kit están en `../commands/`.
