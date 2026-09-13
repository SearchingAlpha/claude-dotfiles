"""
example_eval.py — Plantilla de evaluación de agentes con Inspect AI.

Define un dataset de escenarios de prueba, una cadena de resolución con
herramientas y un scorer que califica el comportamiento del agente.
"""

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import match
from inspect_ai.solver import chain, generate, system_message, use_tools
from inspect_ai.tool import tool


@tool
def check_order_status():
    """Herramienta simulada de consulta de pedidos."""
    async def execute(order_id: str):
        if order_id == "ORD-123":
            return "Status: DELIVERED, Date: 2026-09-10"
        return "Status: NOT_FOUND"
    return execute


@task
def order_agent_benchmark():
    """Benchmark para evaluar precisión y uso de herramientas del agente."""
    dataset = [
        Sample(
            input="¿Cuál es el estado del pedido ORD-123?",
            target="DELIVERED"
        ),
        Sample(
            input="¿Cuál es el estado del pedido ORD-999?",
            target="NOT_FOUND"
        )
    ]

    return Task(
        dataset=dataset,
        plan=chain(
            system_message("Eres un agente de operaciones. Usa las herramientas para verificar datos antes de responder."),
            use_tools(check_order_status()),
            generate()
        ),
        scorer=match(location="any")
    )
