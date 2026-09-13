# Harness de Evaluación Formal con Inspect AI (UK AISI)

Este directorio contiene una plantilla lista para usar del framework **Inspect AI** para evaluar y benchmarkear agentes de IA de forma reproducible y con rigor científico.

## ¿Cuándo usar este harness?

Úsalo cuando estés construyendo o ajustando un agente (por ejemplo, el agente operador de ShortLoad o modelos para despliegues de clientes/FDE) y quieras responder con datos objetivos:
- ¿El nuevo prompt o modelo resuelve más tareas que la versión anterior?
- ¿El agente alucina datos cuando consulta APIs o bases de datos?
- ¿Qué tasa de éxito tiene en casos límite?

## Estructura

```
eval-inspect/
├── README.md             # Esta guía
├── example_eval.py       # Tarea de evaluación de ejemplo lista para ejecutar
└── requirements.txt      # inspect-ai
```

## Instalación rápida

```bash
pip install inspect-ai
```

## Ejecución y Dashboard

Para lanzar la evaluación:
```bash
inspect eval example_eval.py --model anthropic/claude-3-5-sonnet-20241022
```

Para abrir el panel visual interactivo de resultados:
```bash
inspect view
```
Esto abrirá un dashboard en `http://localhost:7575` con todas las trazas de ejecución, llamadas a herramientas, tokens consumidos y puntuaciones.
