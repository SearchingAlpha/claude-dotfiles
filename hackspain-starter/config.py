"""Configuración central del starter-kit. Lee variables de entorno desde .env.

Todos los módulos importan de aquí para no repetir lógica de configuración.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()  # carga .env en os.environ si existe
except ImportError:
    # python-dotenv es opcional; si no está, se usan las env vars del sistema.
    pass


# --- Proveedor del LLM -------------------------------------------------------
# "anthropic" -> API oficial de Anthropic (ANTHROPIC_API_KEY).
# "gemini"    -> Google Gemini API (GEMINI_API_KEY). Patrocinador destacado de HackSpain.
# "openai"    -> OpenAI o proxy compatible (OPENAI_API_KEY, opcional LLM_BASE_URL).
# "aws"       -> Claude Platform on AWS (AnthropicAWS). pip install "anthropic[aws]"
# "bedrock"   -> Amazon Bedrock (AnthropicBedrock). pip install "anthropic[bedrock]"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()

# Modelo por defecto según proveedor.
_DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "gemini": "gemini-2.5-pro",
    "openai": "gpt-4o",
    "bedrock": "anthropic.claude-opus-5",
    "aws": "claude-opus-5",
}
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", _DEFAULT_MODELS.get(LLM_PROVIDER, "claude-opus-5"))
LLM_MODEL = os.getenv("LLM_MODEL", CLAUDE_MODEL)

# Base URL opcional para proxies como api.hackspain.com o LiteLLM
LLM_BASE_URL = os.getenv("LLM_BASE_URL")

# Techo de tokens de salida por respuesta. 4096 va sobrado para un demo.
MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "4096"))

# Claves de proveedores adicionales
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# --- fal.ai ------------------------------------------------------------------
# flux/schnell = rápido y barato, perfecto para demos. Para más calidad: fal-ai/flux/dev
FAL_IMAGE_MODEL = os.getenv("FAL_IMAGE_MODEL", "fal-ai/flux/schnell")
