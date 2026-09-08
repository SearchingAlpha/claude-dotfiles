"""Agente de ejemplo que ata los tres sponsors:

    Claude (razonamiento + tool-calling)  +  Exa (buscar)  +  fal.ai (imágenes)

Dos patrones que cubren el 90% de lo que se pide en un hackathon:
  1) `ask()`        -> agente que decide solo cuándo buscar o generar imágenes.
  2) `rag_answer()` -> RAG clásico: recuperar con Exa, responder citando con Claude.

Ejecuta `python agent.py` para una demo por consola.
"""
import json

import llm
import search
import media


# --- Definición de las tools que Claude puede llamar -------------------------
TOOLS = [
    {
        "name": "web_search",
        "description": (
            "Busca información actual en la web. Úsalo para hechos recientes, "
            "datos concretos, o cualquier cosa que no sepas con certeza."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "La consulta de búsqueda"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "generate_image",
        "description": "Genera una imagen a partir de una descripción y devuelve su URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Descripción de la imagen (en inglés suele dar mejor resultado)",
                },
            },
            "required": ["prompt"],
        },
    },
]


def dispatch(name, tool_input):
    """Ejecuta la tool que Claude ha pedido. Devuelve siempre un str."""
    if name == "web_search":
        results = search.web_search(tool_input["query"], num_results=5)
        return json.dumps(results, ensure_ascii=False)
    if name == "generate_image":
        return media.generate_image(tool_input["prompt"])
    return f"Tool desconocida: {name}"


SYSTEM = (
    "Eres un asistente para un equipo de hackathon. Tienes herramientas para buscar "
    "en la web y generar imágenes. Usa web_search cuando necesites información actual "
    "y cita las URLs. Responde en el idioma del usuario y ve al grano."
)


def ask(user_input, verbose=True):
    """El agente decide por sí mismo qué tools usar."""
    return llm.run_agent(user_input, TOOLS, dispatch, system=SYSTEM, verbose=verbose)


def rag_answer(question, num_sources=5):
    """RAG: recupera fuentes con Exa y deja que Claude responda citándolas.

    Devuelve (respuesta, fuentes). Este patrón es más fiable que dejar al modelo
    'recordar' hechos, y las citas dan credibilidad en la demo.
    """
    results = search.web_search(question, num_results=num_sources)
    sources = "\n\n".join(
        f"[{i + 1}] {r['title']} — {r['url']}\n"
        f"{' '.join(r['highlights']) if r['highlights'] else r['text'][:800]}"
        for i, r in enumerate(results)
    )
    prompt = (
        f"Pregunta: {question}\n\n"
        f"Fuentes:\n{sources}\n\n"
        "Responde basándote SOLO en las fuentes. Cita con [n]. "
        "Si las fuentes no bastan para responder, dilo claramente."
    )
    answer = llm.chat(
        prompt,
        system="Respondes con precisión y citas tus fuentes con [n]. Nada de inventar.",
    )
    return answer, results


if __name__ == "__main__":
    print("=== Agente (Claude + Exa + fal) ===")
    print(ask("Busca qué es HackSpain 2026 y resúmelo en 3 frases."))
    print("\n=== RAG ===")
    ans, srcs = rag_answer("¿Cuándo y dónde se celebra HackSpain 2026?")
    print(ans)
