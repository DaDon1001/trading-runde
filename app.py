"""Multi-Agent-Trading-Chat: mehrere Grok-Agenten diskutieren mit dir.

Start:  streamlit run app.py
"""
import os
import time
from datetime import date

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

from agents import AGENTS, COMMON, MODERATOR

load_dotenv()
MOCK = os.getenv("MOCK", "0") == "1"
MODEL = os.getenv("XAI_MODEL", "grok-4.7")

st.set_page_config(page_title="Trading-Runde", page_icon="📊")
st.title("📊 Trading-Runde")
st.caption("Mehrere Grok-Agenten diskutieren mit dir. Keine Finanzberatung.")

if not MOCK and not os.getenv("XAI_API_KEY"):
    st.error("XAI_API_KEY fehlt. Trag ihn in die Datei .env ein (siehe README) oder starte mit MOCK=1.")
    st.stop()

client = None if MOCK else OpenAI(api_key=os.environ["XAI_API_KEY"], base_url="https://api.x.ai/v1")

if "chat" not in st.session_state:
    st.session_state.chat = []  # Liste von {"name", "avatar", "text"}

# Seitenleiste: wer ist dabei?
with st.sidebar:
    st.header("Agenten")
    active = [a for a in AGENTS if st.checkbox(f"{a['avatar']} {a['name']}", value=True, help=a["role"])]
    st.header("Recherche")
    use_web = st.toggle("🔎 Websuche (News, Kurse)", value=True)
    use_x = st.toggle("𝕏 X-Suche (Stimmung, Breaking News)", value=True)
    st.caption("Jede Suche kostet extra API-Guthaben.")
    st.divider()
    if st.button("Chat leeren"):
        st.session_state.chat = []
        st.rerun()


def transcript() -> str:
    return "\n\n".join(f"{m['name']}: {m['text']}" for m in st.session_state.chat)


def sources_of(resp) -> list[str]:
    """Holt die zitierten Links aus einer Responses-API-Antwort."""
    urls = []
    for item in getattr(resp, "output", []) or []:
        for part in getattr(item, "content", None) or []:
            for ann in getattr(part, "annotations", None) or []:
                url = getattr(ann, "url", None)
                if url and url not in urls:
                    urls.append(url)
    return urls[:5]


def speak(agent: dict) -> str:
    if MOCK:
        time.sleep(0.3)
        return f"(Testantwort von {agent['name']} auf die bisherige Diskussion)"
    system = f"{COMMON}\n\nDeine Rolle: {agent['name']}. {agent['role']}"
    user = (f"Heute ist {date.today():%d.%m.%Y}.\n\nBisheriger Chatverlauf:\n\n{transcript()}\n\n"
            f"Du bist jetzt dran, {agent['name']}. Schreib nur deinen Beitrag, ohne Namenspräfix.")
    tools = []
    if use_web:
        tools.append({"type": "web_search"})
    if use_x:
        tools.append({"type": "x_search"})
    extra = {"tools": tools} if tools else {}
    resp = client.responses.create(
        model=MODEL,
        input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        **extra,
    )
    text = (resp.output_text or "").strip()
    links = sources_of(resp)
    if links:
        text += "\n\n*Quellen:* " + " · ".join(f"[{i + 1}]({u})" for i, u in enumerate(links))
    return text


def run_round(agents: list[dict]) -> None:
    for agent in agents:
        with st.chat_message(agent["name"], avatar=agent["avatar"]):
            with st.spinner(f"{agent['name']} schreibt …"):
                try:
                    text = speak(agent)
                except Exception as e:  # z. B. falscher Key, kein Guthaben, Netzwerk
                    detail = getattr(getattr(e, "response", None), "text", "") or ""
                    text = f"⚠️ Fehler bei der Grok-Anfrage: {e}\n\n`{detail[:500]}`"
            st.markdown(f"**{agent['name']}**  \n{text}")
        st.session_state.chat.append({"name": agent["name"], "avatar": agent["avatar"], "text": text})


# Bisherigen Verlauf anzeigen
for m in st.session_state.chat:
    with st.chat_message(m["name"], avatar=m["avatar"]):
        st.markdown(f"**{m['name']}**  \n{m['text']}")

# Steuerung
c1, c2 = st.columns(2)
more = c1.button("🔁 Weiter diskutieren", disabled=not st.session_state.chat, use_container_width=True)
summary = c2.button("🧭 Fazit ziehen", disabled=not st.session_state.chat, use_container_width=True)

prompt = st.chat_input("Deine Nachricht an die Runde, z. B. 'Lohnt sich ein Einstieg bei Nvidia nach den Zahlen?'")

if prompt:
    st.session_state.chat.append({"name": "Daniel", "avatar": "🧑", "text": prompt})
    with st.chat_message("Daniel", avatar="🧑"):
        st.markdown(f"**Daniel**  \n{prompt}")
    run_round(active)
elif more:
    run_round(active)
elif summary:
    run_round([MODERATOR])

if prompt or more or summary:
    st.rerun()  # Knöpfe mit neuem Stand neu zeichnen
