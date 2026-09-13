"""Wrapper de Claude: chat simple + un bucle de agente (tool-calling) transparente.

Diseño a propósito SIN magia: el bucle de tools está escrito a mano y se puede
leer de arriba a abajo. Cuando el agente se atasque en el hackathon, quieres poder
ver exactamente qué está pasando (por eso `verbose=True`).

Ejecuta `python llm.py` para una prueba de humo.
"""
import json

import anthropic

import config

_client = None


def get_client():
    """Devuelve el cliente según LLM_PROVIDER. Se cachea."""
    global _client
    if _client is not None:
        return _client

    provider = config.LLM_PROVIDER
    if provider == "anthropic":
        _client = anthropic.Anthropic()  # lee ANTHROPIC_API_KEY
    elif provider == "aws":
        # Claude Platform on AWS (paridad total con la API oficial, IDs sin prefijo)
        from anthropic import AnthropicAWS  # pip install "anthropic[aws]"
        _client = AnthropicAWS()  # necesita AWS_REGION y ANTHROPIC_AWS_WORKSPACE_ID
    elif provider == "bedrock":
        from anthropic import AnthropicBedrock  # pip install "anthropic[bedrock]"
        _client = AnthropicBedrock()  # credenciales AWS estándar; región AWS_REGION
    elif provider in ("openai", "gemini"):
        try:
            import openai
        except ImportError:
            raise ImportError("Para usar 'openai' o 'gemini' como LLM_PROVIDER: pip install openai")
        
        if provider == "gemini":
            # Endpoint OpenAI-compatible nativo de Google Gemini
            base_url = config.LLM_BASE_URL or "https://generativelanguage.googleapis.com/v1beta/openai/"
            api_key = config.GEMINI_API_KEY or config.OPENAI_API_KEY
            _client = openai.OpenAI(api_key=api_key, base_url=base_url)
        else:
            _client = openai.OpenAI(
                api_key=config.OPENAI_API_KEY,
                base_url=config.LLM_BASE_URL or None,
            )
    else:
        raise ValueError(f"LLM_PROVIDER desconocido: {provider!r}")
    return _client


def resolve_model(model=None):
    """Normaliza el ID del modelo según el proveedor."""
    model = model or config.LLM_MODEL
    if config.LLM_PROVIDER == "bedrock" and not model.startswith("anthropic."):
        return "anthropic." + model
    return model


def chat(prompt, system=None, model=None, max_tokens=None):
    """Una sola pregunta -> texto. Funciona con Claude, Gemini u OpenAI."""
    client = get_client()
    provider = config.LLM_PROVIDER
    resolved = resolve_model(model)
    max_tokens = max_tokens or config.MAX_TOKENS

    if provider in ("openai", "gemini"):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=resolved,
            messages=messages,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    # Proveedores Anthropic (anthropic, aws, bedrock)
    kwargs = {
        "model": resolved,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    return "".join(b.text for b in resp.content if b.type == "text")


def run_agent(user_input, tools, dispatch, system=None, model=None,
              max_tokens=None, max_steps=8, verbose=True):
    """Bucle de agente manual: Claude llama tools hasta que termina.

    Args:
        user_input: el mensaje del usuario (str).
        tools:      lista de definiciones de tools (JSON schema de Anthropic).
        dispatch:   función (name, input_dict) -> str que ejecuta la tool.
        max_steps:  cortafuegos anti-bucle. Si se alcanza, algo va mal.
        verbose:    imprime cada llamada a tool (clave para depurar en directo).

    Devuelve el texto final de Claude.
    """
    client = get_client()
    model = resolve_model(model)
    max_tokens = max_tokens or config.MAX_TOKENS
    messages = [{"role": "user", "content": user_input}]

    for step in range(1, max_steps + 1):
        kwargs = {"model": model, "max_tokens": max_tokens,
                  "tools": tools, "messages": messages}
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)

        # Un tool de servidor de larga duración puede pausar el turno: reenviar.
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue

        tool_uses = [b for b in resp.content if b.type == "tool_use"]

        # Claude terminó (no quiere más tools) -> devolvemos el texto.
        if resp.stop_reason == "end_turn" or not tool_uses:
            return "".join(b.text for b in resp.content if b.type == "text")

        # Guardamos el turno del asistente (incluye los bloques tool_use).
        messages.append({"role": "assistant", "content": resp.content})

        # Ejecutamos cada tool y devolvemos los resultados en un mensaje user.
        results = []
        for tu in tool_uses:
            if verbose:
                print(f"  [step {step}] tool={tu.name} input={json.dumps(tu.input, ensure_ascii=False)}")
            try:
                out = dispatch(tu.name, tu.input)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,   # DEBE coincidir con el id del bloque tool_use
                    "content": str(out),
                })
            except Exception as e:
                # Devolver el error a Claude (no reventar) le deja reintentar/corregir.
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": f"Error ejecutando {tu.name}: {e}",
                    "is_error": True,
                })
        messages.append({"role": "user", "content": results})

    return ("(Se alcanzó max_steps sin respuesta final. El agente probablemente "
            "está en bucle: revisa las descripciones de las tools o sube max_steps.)")


if __name__ == "__main__":
    print("Proveedor:", config.LLM_PROVIDER, "| Modelo:", resolve_model())
    print(chat("Dime en una frase qué es un hackathon."))
