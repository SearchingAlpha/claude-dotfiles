"""Demo web de un solo archivo con Streamlit.

Arranca con:   streamlit run app.py

Cuatro pestañas, una por capacidad, para que en el hackathon tengas algo que
enseñar en 5 minutos y a partir de ahí construyas tu idea encima.
"""
import streamlit as st

import agent
import config
import llm
import media
import search
from trace import AuditTracer

st.set_page_config(page_title="HackSpain Starter", page_icon="🚀", layout="centered")
st.title("🚀 HackSpain Starter")
st.caption(f"LLM: {config.LLM_PROVIDER} ({config.LLM_MODEL}) + Exa + fal.ai + Trazabilidad")

tab_chat, tab_search, tab_image, tab_agent, tab_trace = st.tabs(
    ["💬 LLM", "🔎 Buscar (Exa)", "🎨 Imagen (fal)", "🤖 Agente + RAG", "🛡️ Traza (Chain of Work)"]
)

with tab_chat:
    st.subheader(f"Chat con {config.LLM_PROVIDER.capitalize()}")
    prompt = st.text_area("Tu mensaje", "Explícame qué es un hackathon en 2 frases.")
    if st.button("Enviar", key="chat"):
        with st.spinner("Pensando..."):
            st.write(llm.chat(prompt))

with tab_search:
    st.subheader("Búsqueda web (Exa)")
    query = st.text_input("Consulta", "startups de IA en España 2026")
    if st.button("Buscar", key="search"):
        with st.spinner("Buscando..."):
            for r in search.web_search(query, num_results=5):
                st.markdown(f"**[{r['title']}]({r['url']})**")
                if r["highlights"]:
                    st.write(r["highlights"][0])

with tab_image:
    st.subheader("Generar imagen (fal.ai)")
    img_prompt = st.text_input(
        "Descripción (en inglés funciona mejor)",
        "a friendly robot mascot for a hackathon, flat vector, vibrant colors",
    )
    if st.button("Generar", key="image"):
        with st.spinner("Generando..."):
            st.image(media.generate_image(img_prompt))

with tab_agent:
    st.subheader("Agente autónomo + RAG")
    st.caption("El agente decide solo si buscar en la web o generar una imagen.")
    task = st.text_input("Pídele algo", "Busca las fechas de HackSpain 2026 y resúmelas.")
    if st.button("Ejecutar agente", key="agent"):
        with st.spinner("Trabajando (mira la consola para ver las tools)..."):
            st.write(agent.ask(task, verbose=True))

    st.divider()
    rag_q = st.text_input("O una pregunta con RAG (respuesta citada)", "¿Dónde se celebra HackSpain 2026?")
    if st.button("Responder con fuentes", key="rag"):
        with st.spinner("Recuperando y respondiendo..."):
            answer, sources = agent.rag_answer(rag_q)
            st.write(answer)
            with st.expander("Fuentes"):
                for i, s in enumerate(sources, 1):
                    st.markdown(f"[{i}] [{s['title']}]({s['url']})")

with tab_trace:
    st.subheader("🛡️ Trazabilidad y Guardarraíl Determinista")
    st.caption("Patrón enterprise (Maisa, HappyRobot, Embat, Prosper AI): el LLM interpreta, el código valida y audita.")

    col1, col2 = st.columns([3, 1])
    with col1:
        case_type = st.selectbox(
            "Caso operacional simulado",
            [
                "Conciliación de Factura (Embat / Maisa)",
                "Check-Call & Reagendamiento Logístico (HappyRobot)",
                "Verificación de Seguro & Cita Médica (Prosper AI)",
            ]
        )
    with col2:
        confidence = st.slider("Confianza del modelo", min_value=0.50, max_value=1.0, value=0.94, step=0.02)

    if st.button("Ejecutar Flujo con Auditoría", key="trace_run"):
        tracer = AuditTracer(case_type.split(" ")[0].lower())
        tracer.record_step("ingesta_evento", {"tipo": case_type, "timestamp": "2026-09-18T18:00:00Z"})
        tracer.record_step("extraccion_llm", {"modelo": config.LLM_MODEL, "confianza_base": confidence})
        
        # Validación determinista
        is_safe = confidence >= 0.90
        tracer.record_step(
            "guardarail_determinista",
            {"regla": "umbral_minimo_autonomia >= 0.90", "aprobado": is_safe},
            status="PASS" if is_safe else "WARNING"
        )

        proposal = tracer.propose_action(
            action_type="escritura_sistema_registro",
            payload={"accion": "post_ledger_or_booking", "id": "TX-2026-88"},
            confidence=confidence,
            confidence_threshold=0.90,
            reversible=True,
        )

        st.success(f"Ejecución completada en {tracer.steps[-1].duration_ms:.1f}ms")
        st.code(tracer.summary_table())

        st.markdown("#### Estado de la Propuesta:")
        if proposal.requires_human_approval:
            st.warning("⚠️ Requiere Aprobación Humana (Confianza < 90%). La acción no se ejecutó a ciegas.")
        else:
            st.info("✅ Aprobación Autónoma (Confianza >= 90%). Escritura permitida con trazabilidad.")
