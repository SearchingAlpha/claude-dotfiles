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
# "anthropic" -> API oficial de Anthropic (ANTHROPIC_API_KEY). Lo más simple.
# "aws"       -> Claude Platform on AWS (AnthropicAWS). pip install "anthropic[aws]"
#                Requiere AWS_REGION y ANTHROPIC_AWS_WORKSPACE_ID.
# "bedrock"   -> Amazon Bedrock (AnthropicBedrock). pip install "anthropic[bedrock]"
#                Usa credenciales AWS estándar. (Es lo que usas en NarrativeAgent.)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()

# Modelo por defecto. claude-opus-5 = el más capaz.
# Para iterar MÁS RÁPIDO y BARATO durante el hackathon: pon CLAUDE_MODEL=claude-sonnet-5.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5")

# Techo de tokens de salida por respuesta. 4096 va sobrado para un demo.
MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "4096"))

# --- fal.ai ------------------------------------------------------------------
# flux/schnell = rápido y barato, perfecto para demos. Para más calidad: fal-ai/flux/dev
FAL_IMAGE_MODEL = os.getenv("FAL_IMAGE_MODEL", "fal-ai/flux/schnell")
