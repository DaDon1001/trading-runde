"""Multi-Agent-Trading-Chat: mehrere Grok-Agenten diskutieren mit dir.

Start:  streamlit run app.py
"""

import os
import re
import time
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

from agents import AGENTS, MODERATOR, instructions
from market import (
    CUSTOM,
    PERIODS,
    PRESETS,
    MarketUnavailable,
    failed,
    fmt_number,
    fmt_pct,
    format_context,
    fetch_market,
    normalize_symbol,
    price_digits,
    sample_market,
)

load_dotenv()
MOCK = os.getenv("MOCK", "0") == "1"
MODEL = os.getenv("XAI_MODEL", "grok-4.7")
_SECRET = re.compile(r"xai-[A-Za-z0-9_\-]{4,}")

st.set_page_config(page_title="Trading-Runde", page_icon="📊")
st.title("📊 Trading-Runde")
st.caption("Mehrere Grok-Agenten diskutieren mit dir. Keine Finanzberatung.")

if "chat" not in st.session_state:
    st.session_state.chat = []  # Liste von {"name", "avatar", "text"}
if "market_nonce" not in st.session_state:
    st.session_state.market_nonce = 0
if "market" not in st.session_state:
    st.session_state.market = None

client = None
if not MOCK and os.getenv("XAI_API_KEY"):
    client = OpenAI(
        api_key=os.environ["XAI_API_KEY"],
        base_url="https://api.x.ai/v1",
        timeout=90.0,
        max_retries=1,
    )


def trader_name() -> str:
    return (os.getenv("TRADER_NAME", "Daniel").strip() or "Daniel")[:40]


def redact(text: str) -> str:
    return _SECRET.sub("xai-…", text or "")


# Seitenleiste: wer ist dabei?
with st.sidebar:
    st.header("Agenten")
    active = []
    for agent in AGENTS:
        if st.checkbox(
            f"{agent['avatar']} {agent['name']}",
            value=True,
            help=agent["role"],
            key=f"agent-{agent['name']}",
        ):
            active.append(agent)
    if MOCK:
        st.caption(f"{len(active)} aktiv · Testmodus, keine API-Aufrufe.")
    else:
        st.caption(f"{len(active)} aktiv · jede Stimme ein API-Aufruf.")
    st.header("Recherche")
    use_web = st.toggle("🔎 Websuche (News, Kurse)", value=not MOCK, disabled=MOCK)
    use_x = st.toggle("𝕏 X-Suche (Stimmung, Breaking News)", value=not MOCK, disabled=MOCK)
    if MOCK:
        st.caption("Im Testmodus bleibt die Recherche aus.")
    else:
        st.caption("Jede Suche kostet extra API-Guthaben.")
    st.divider()
    if st.button("Chat leeren"):
        st.session_state.chat = []
        st.rerun()


def load_snapshot(symbol: str, period: str) -> dict:
    """Ein Abruf pro Symbol/Zeitraum. Ein Chat-Rerun holt nicht neu."""
    sym = normalize_symbol(symbol)
    key = (sym, period, st.session_state.market_nonce, MOCK)
    cached = st.session_state.get("market_cache")
    if cached and cached.get("key") == key:
        return cached["snap"]
    if MOCK:
        snap = sample_market(symbol, period)
    else:
        try:
            with st.spinner("Kurse werden geladen …"):
                snap = fetch_market(symbol, period)
        except MarketUnavailable as exc:
            snap = failed(sym or symbol, str(exc))
        except Exception:
            snap = failed(
                sym or symbol,
                "Kursdaten konnten nicht gelesen werden. Die Runde kann ohne Live-Kurse weiterreden.",
            )
    st.session_state.market_cache = {"key": key, "snap": snap}
    return snap


def render_market() -> None:
    st.subheader("Kurs")
    if MOCK:
        st.caption("MOCK=1 · Beispieldaten, kein Netz, keine Grok-Anfrage.")
    labels = dict(PRESETS)
    preset_symbols = [sym for sym, _label in PRESETS]
    col_symbol, col_period, col_refresh = st.columns([2.2, 1.3, 1], vertical_alignment="bottom")
    with col_symbol:
        preset = st.selectbox(
            "Symbol",
            preset_symbols,
            index=preset_symbols.index("NVDA"),
            format_func=lambda sym: labels[sym],
            key="preset_symbol",
            help="Aktie, Index oder Krypto. BTC und ETH werden zu BTC-USD und ETH-USD.",
        )
    with col_period:
        period = st.selectbox(
            "Zeitraum",
            list(PERIODS),
            index=list(PERIODS).index("3mo"),
            format_func=lambda key: PERIODS[key],
            key="period",
        )
    with col_refresh:
        if st.button("Aktualisieren", use_container_width=True):
            st.session_state.market_nonce += 1
            st.session_state.market_cache = None
            st.rerun()

    symbol = preset
    if preset == CUSTOM:
        symbol = st.text_input(
            "Yahoo-Symbol",
            placeholder="z. B. AMD, ASML.AS, ETH-USD",
            key="custom_symbol",
        ).strip()
        if not symbol:
            snap = failed("", "Gib ein Symbol ein, z. B. NVDA oder BTC-USD.")
            st.session_state.market = snap
            st.info(snap["error"])
            return

    snap = load_snapshot(symbol, period)
    st.session_state.market = snap
    if not snap["ok"]:
        st.warning(snap["error"])
        return

    if preset == CUSTOM and snap["symbol"] != symbol.upper():
        st.caption(f"Verwendet: {snap['symbol']}")

    digits = price_digits(float(snap["price"]))
    price_text = f"{fmt_number(snap['price'], digits)} {snap['currency']}".strip()
    # Exakt 0 bleibt grau. Jeder andere String würde Streamlit als Anstieg färben.
    day_delta = 0 if abs(snap["change_pct"]) < 0.005 else fmt_pct(snap["change_pct"])
    m1, m2, m3 = st.columns(3)
    m1.metric("Letzter Kurs", price_text, delta=day_delta)
    m2.metric("Vortag", f"{fmt_number(snap['prev_close'], digits)} {snap['currency']}".strip())
    m3.metric(snap["period_label"], fmt_pct(snap["period_change_pct"]))
    frame = pd.DataFrame(
        {"Datum": pd.to_datetime(snap["dates"]), "Schluss": snap["closes"]}
    )
    # Eigene Achse ohne Nullpunkt, sonst wirkt ein Kurs von 60.000 fast flach.
    chart = (
        alt.Chart(frame)
        .mark_line()
        .encode(
            x=alt.X("Datum:T", title=None, axis=alt.Axis(format="%d.%m.", tickCount=6)),
            y=alt.Y("Schluss:Q", title=None, scale=alt.Scale(zero=False, nice=True)),
        )
        .properties(height=260)
    )
    try:
        st.altair_chart(chart, width="stretch")
    except Exception:
        st.caption("Chart konnte nicht gezeichnet werden. Die Zahlen stehen den Agenten trotzdem zur Verfügung.")
    high = fmt_number(snap["high"], digits)
    low = fmt_number(snap["low"], digits)
    note = "Beispieldaten, kein Live-Kurs" if snap["mock"] else "Tagesdaten, ohne API-Key"
    st.caption(f"{snap['name']} · Hoch {high} · Tief {low} · {snap['source']} · {note} · Stand {snap['as_of']}")


def transcript(max_chars: int = 12_000) -> str:
    chunks = [f"{message['name']}: {message['text']}" for message in st.session_state.chat]
    chosen: list[str] = []
    total = 0
    for chunk in reversed(chunks):
        extra = len(chunk) + 2
        if chosen and total + extra > max_chars:
            break
        chosen.append(chunk)
        total += extra
    chosen.reverse()
    text = "\n\n".join(chosen)
    if len(chosen) < len(chunks):
        text = "…(älterer Verlauf gekürzt)…\n\n" + text
    return text


def sources_of(resp) -> list[str]:
    """Holt die zitierten Links aus einer Responses-API-Antwort."""
    urls: list[str] = []

    def add(url) -> None:
        if isinstance(url, str) and url.startswith("http") and url not in urls:
            urls.append(url)

    for item in getattr(resp, "output", []) or []:
        add(getattr(item, "url", None))
        for part in getattr(item, "content", None) or []:
            for ann in getattr(part, "annotations", None) or []:
                add(getattr(ann, "url", None))
    for cite in getattr(resp, "citations", None) or []:
        if isinstance(cite, str):
            add(cite)
        else:
            add(getattr(cite, "url", None))
    return urls[:5]


def describe_error(exc: BaseException) -> str:
    detail = ""
    response = getattr(exc, "response", None)
    if response is not None:
        raw = getattr(response, "text", "")
        try:
            detail = raw() if callable(raw) else raw
        except Exception:
            detail = ""
        if not isinstance(detail, str):
            detail = str(detail or "")
    message = redact(str(exc)).strip() or "unbekannter Fehler"
    extra = redact(detail).strip()
    if extra and extra not in message:
        message = f"{message}\n\n`{extra[:400]}`"
    return f"⚠️ Fehler bei der Grok-Anfrage: {message}"


def speak(agent: dict) -> str:
    if MOCK:
        time.sleep(0.15)
        snap = st.session_state.get("market") or {}
        if snap.get("ok"):
            digits = price_digits(float(snap["price"]))
            kurs = f"{fmt_number(snap['price'], digits)} {snap.get('currency') or ''}".strip()
            return (
                f"(Testantwort von {agent['name']} auf die bisherige Diskussion. "
                f"Kontext: {snap['symbol']} {kurs}.)"
            )
        reason = snap.get("error") or "kein Symbol"
        return f"(Testantwort von {agent['name']}. Keine verwertbaren Kurse: {reason})"
    system = instructions(agent, trader_name(), use_web=use_web, use_x=use_x)
    user = (
        f"Heute ist {date.today():%d.%m.%Y}.\n\n"
        f"{format_context(st.session_state.get('market'))}\n\n"
        f"Bisheriger Chatverlauf:\n\n{transcript()}\n\n"
        f"Du bist jetzt dran, {agent['name']}. Schreib nur deinen Beitrag, ohne Namenspräfix."
    )
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
    text = (getattr(resp, "output_text", None) or "").strip()
    if not text:
        text = "⚠️ Keine Antwort vom Modell erhalten."
    links = sources_of(resp)
    if links:
        text += "\n\n*Quellen:* " + " · ".join(f"[{i + 1}]({url})" for i, url in enumerate(links))
    return text


def run_round(agents: list[dict]) -> None:
    if not agents:
        st.info("Keine Agenten aktiv. Schalte links mindestens einen ein.")
        return
    for agent in agents:
        with st.chat_message(agent["name"], avatar=agent["avatar"]):
            with st.spinner(f"{agent['name']} schreibt …"):
                try:
                    text = speak(agent)
                except Exception as exc:  # z. B. falscher Key, kein Guthaben, Netzwerk
                    text = describe_error(exc)
            st.markdown(f"**{agent['name']}**  \n{text}")
        st.session_state.chat.append({"name": agent["name"], "avatar": agent["avatar"], "text": text})


render_market()

model_ready = MOCK or client is not None
if not model_ready:
    st.error(
        "XAI_API_KEY fehlt. Trag ihn in die Datei .env ein (siehe README) oder starte mit MOCK=1. "
        "Kurse oben funktionieren auch ohne Key."
    )
    st.stop()

# Bisherigen Verlauf anzeigen
who = trader_name()
for message in st.session_state.chat:
    with st.chat_message(message["name"], avatar=message["avatar"]):
        st.markdown(f"**{message['name']}**  \n{message['text']}")

# Steuerung
c1, c2 = st.columns(2)
more = c1.button(
    "🔁 Weiter diskutieren",
    disabled=not st.session_state.chat or not active,
    use_container_width=True,
)
summary = c2.button("🧭 Fazit ziehen", disabled=not st.session_state.chat, use_container_width=True)

prompt = st.chat_input("Deine Nachricht an die Runde, z. B. 'Lohnt sich ein Einstieg bei Nvidia nach den Zahlen?'")

if prompt:
    st.session_state.chat.append({"name": who, "avatar": "🧑", "text": prompt})
    with st.chat_message(who, avatar="🧑"):
        st.markdown(f"**{who}**  \n{prompt}")
    run_round(active)
elif more:
    run_round(active)
elif summary:
    run_round([MODERATOR])

if prompt or more or summary:
    st.rerun()  # Knöpfe mit neuem Stand neu zeichnen
