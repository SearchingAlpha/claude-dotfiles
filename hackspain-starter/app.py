"""Demo web de un solo archivo con Streamlit.

Arranca con:   streamlit run app.py

Cuatro pestañas, una por capacidad, para que en el hackathon tengas algo que
enseñar en 5 minutos y a partir de ahí construyas tu idea encima.
"""
import streamlit as st

import agent
import llm
import media
import search

st.set_page_config(page_title="HackSpain Starter", page_icon="🚀", layout="centered")
st.title("🚀 HackSpain Starter")
st.caption("Claude + Exa + fal.ai — esqueleto para arrancar en minutos")

tab_chat, tab_search, tab_image, tab_agent = st.tabs(
    ["💬 Claude", "🔎 Buscar (Exa)", "🎨 Imagen (fal)", "🤖 Agente + RAG"]
)

with tab_chat:
    st.subheader("Chat con Claude")
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
