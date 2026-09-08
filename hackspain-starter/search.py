"""Búsqueda web con Exa (search neural / RAG).

Exa lee la clave de la env var EXA_API_KEY automáticamente.
Ejecuta `python search.py` para una prueba de humo.
"""
from exa_py import Exa

_exa = None


def get_exa():
    global _exa
    if _exa is None:
        _exa = Exa()  # lee EXA_API_KEY
    return _exa


def web_search(query, num_results=5, use_highlights=True):
    """Busca en la web y devuelve una lista de dicts {title, url, highlights, text}.

    `highlights` son los fragmentos más relevantes (ideal para dárselos al LLM).
    """
    exa = get_exa()
    contents = {"text": True}
    if use_highlights:
        contents["highlights"] = True

    try:
        # API actual de exa-py: contents como parámetro de search().
        resp = exa.search(query, type="auto", num_results=num_results, contents=contents)
    except TypeError:
        # Fallback para SDKs más antiguos donde search() no acepta `contents`.
        resp = exa.search_and_contents(
            query, type="auto", num_results=num_results,
            text=True, highlights=use_highlights,
        )

    out = []
    for r in resp.results:
        text = getattr(r, "text", None) or ""
        out.append({
            "title": getattr(r, "title", None),
            "url": getattr(r, "url", None),
            "highlights": getattr(r, "highlights", None) or [],
            "text": text[:2000],  # recortamos para no inflar el contexto del LLM
        })
    return out


if __name__ == "__main__":
    for r in web_search("HackSpain 2026 hackathon Madrid", num_results=3):
        print("-", r["title"], "->", r["url"])
