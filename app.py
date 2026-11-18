"""Multi-Agent-Trading-Chat: mehrere Grok-Agenten diskutieren mit dir.

Start:  streamlit run app.py
"""

import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

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
    quote_notes,
    sample_market,
)

load_dotenv(override=True)
MOCK = os.getenv("MOCK", "0") == "1"
MODEL = os.getenv("XAI_MODEL", "grok-4.7")
_SECRET = re.compile(r"xai-[A-Za-z0-9_\-]{4,}")
_QUELLEN_TAIL = re.compile(r"\n+\*Quellen:\*.*\Z", re.DOTALL)
BLANK_REPLY = "⚠️ Keine Antwort vom Modell erhalten."
BERLIN = ZoneInfo("Europe/Berlin")

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


class _FetchMiss(Exception):
    """Fehlschlag, den st.cache_data nicht behalten soll."""

    def __init__(self, snap: dict):
        super().__init__(snap.get("error") or "Kursabruf fehlgeschlagen")
        self.snap = snap


@st.cache_data(ttl=120, show_spinner=False)
def _live_market(symbol: str, period: str, nonce: int) -> dict:
    """Erfolgreiche Kurse etwa 120 Sekunden cachen. nonce ist nur der Aktualisieren-Schlüssel."""
    del nonce
    snap = fetch_market(symbol, period)
    if not snap.get("ok"):
        raise _FetchMiss(snap)
    return snap


def load_snapshot(symbol: str, period: str) -> dict:
    """Lebende Kurse höchstens alle 120 Sekunden neu holen. Fehlschläge nicht cachen."""
    sym = normalize_symbol(symbol)
    if MOCK:
        return sample_market(symbol, period)
    try:
        with st.spinner("Kurse werden geladen …"):
            return _live_market(sym, period, int(st.session_state.market_nonce))
    except _FetchMiss as exc:
        return exc.snap
    except MarketUnavailable as exc:
        return failed(sym or symbol, str(exc))
    except Exception:
        return failed(
            sym or symbol,
            "Kursdaten konnten nicht gelesen werden. Die Runde kann ohne Live-Kurse weiterreden.",
        )


def entry(name: str, avatar: str, text: str, kind: str, sources: list | None = None) -> dict:
    return {
        "name": name,
        "avatar": avatar,
        "text": text,
        "kind": kind,
        "sources": list(sources or []),
    }


def present(message: dict) -> str:
    """Text für die Oberfläche, inklusive Quellen. Die Quellen stehen nicht im Modell-Text."""
    text = (message.get("text") or "").rstrip()
    links: list[str] = []
    for url in message.get("sources") or []:
        if isinstance(url, str) and url.startswith("http") and url not in links:
            links.append(url)
    if links:
        text += "\n\n*Quellen:* " + " · ".join(f"[{i + 1}]({url})" for i, url in enumerate(links))
    return text


def transcript(messages: list | None = None, max_chars: int = 12_000) -> str:
    """Nur kind=msg, ohne Fehler, Hinweise und ohne Quellen-Listen."""
    if messages is None:
        messages = st.session_state.chat
    chunks: list[str] = []
    for message in messages:
        if message.get("kind") != "msg":
            continue
        body = _QUELLEN_TAIL.sub("", message.get("text") or "").strip()
        if not body:
            continue
        chunks.append(f"{message.get('name') or '?'}: {body}")
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


def sources_of(resp) -> list:
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


def tool_args(agent: dict, *, use_web: bool, use_x: bool) -> dict:
    """Moderator recherchiert nicht. Andere Agenten bekommen nur die eingeschalteten Suchen.

    Ohne Werkzeuge fehlt das Feld `tools` ganz. `tools: []` schickt die xAI-API nicht.
    """
    if agent.get("name") == MODERATOR["name"]:
        return {}
    tools = []
    if use_web:
        tools.append({"type": "web_search"})
    if use_x:
        tools.append({"type": "x_search"})
    if not tools:
        return {}
    return {"tools": tools}


def speak(agent: dict, *, use_web: bool, use_x: bool) -> dict:
    if MOCK:
        time.sleep(0.15)
        snap = st.session_state.get("market") or {}
        if snap.get("ok"):
            digits = price_digits(float(snap["price"]))
            kurs = f"{fmt_number(snap['price'], digits)} {snap.get('currency') or ''}".strip()
            text = (
                f"(Testantwort von {agent['name']} auf die bisherige Diskussion. "
                f"Kontext: {snap['symbol']} {kurs}.)"
            )
        else:
            reason = snap.get("error") or "kein Symbol"
            text = f"(Testantwort von {agent['name']}. Keine verwertbaren Kurse: {reason})"
        return entry(agent["name"], agent["avatar"], text, "msg")
    jetzt = datetime.now(BERLIN)
    system = instructions(agent, trader_name(), use_web=use_web, use_x=use_x)
    user = (
        f"Heute ist {jetzt:%d.%m.%Y}, {jetzt:%H:%M} Uhr (Europe/Berlin).\n\n"
        f"{format_context(st.session_state.get('market'))}\n\n"
        f"Bisheriger Chatverlauf:\n\n{transcript()}\n\n"
        f"Du bist jetzt dran, {agent['name']}. Schreib nur deinen Beitrag, ohne Namenspräfix."
    )
    resp = client.responses.create(
        model=MODEL,
        input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        **tool_args(agent, use_web=use_web, use_x=use_x),
    )
    text = (getattr(resp, "output_text", None) or "").strip()
    links = sources_of(resp)
    if not text:
        return entry(agent["name"], agent["avatar"], BLANK_REPLY, "notice", links)
    return entry(agent["name"], agent["avatar"], text, "msg", links)


def run_round(agents: list, *, use_web: bool, use_x: bool) -> None:
    if not agents:
        st.info("Keine Agenten aktiv. Schalte links mindestens einen ein.")
        return
    for agent in agents:
        with st.chat_message(agent["name"], avatar=agent["avatar"]):
            with st.spinner(f"{agent['name']} schreibt …"):
                try:
                    spoken = speak(agent, use_web=use_web, use_x=use_x)
                except Exception as exc:  # z. B. falscher Key, kein Guthaben, Netzwerk
                    spoken = entry(agent["name"], agent["avatar"], describe_error(exc), "error")
            st.markdown(f"**{agent['name']}**  \n{present(spoken)}")
        st.session_state.chat.append(spoken)


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
        if st.button("Aktualisieren", width="stretch"):
            st.session_state.market_nonce += 1
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
    change_help = None
    if snap.get("crypto") and not snap.get("mock"):
        change_help = "Tagesveränderung seit 00:00 UTC."
    elif not snap.get("crypto"):
        change_help = "Nur reguläre Handelszeit, ohne Vor- und Nachbörse."
    m1, m2, m3 = st.columns(3)
    price_label = "Schlusskurs" if snap.get("market_state") == "closed" else "Letzter Kurs"
    m1.metric(price_label, price_text, delta=day_delta, help=change_help)
    m2.metric("Vortag", f"{fmt_number(snap['prev_close'], digits)} {snap['currency']}".strip())
    m3.metric(snap["period_label"], fmt_pct(snap["period_change_pct"]))
    hints = [""] * len(snap["dates"])
    if hints and snap.get("intraday"):
        hints[-1] = "intraday"
    elif hints and snap.get("close_fill"):
        hints[-1] = "letzter Schluss"
    frame = pd.DataFrame(
        {
            "Datum": pd.to_datetime(snap["dates"]),
            "Schluss": snap["closes"],
            "Hinweis": hints,
        }
    )
    # Eigene Achse ohne Nullpunkt, sonst wirkt ein Kurs von 60.000 fast flach.
    chart = (
        alt.Chart(frame)
        .mark_line()
        .encode(
            x=alt.X("Datum:T", title=None, axis=alt.Axis(format="%d.%m.", tickCount=6)),
            y=alt.Y("Schluss:Q", title=None, scale=alt.Scale(zero=False, nice=True)),
            tooltip=["Datum:T", "Schluss:Q", "Hinweis:N"],
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
    status = " · ".join(quote_notes(snap))
    caption = (
        f"{snap['name']} · Hoch {high} · Tief {low} · {snap['source']} · {note} · Stand {snap['as_of']}"
    )
    if status:
        caption = f"{caption} · {status}"
    st.caption(caption)


def main() -> None:
    st.set_page_config(page_title="Trading-Runde", page_icon="📊")
    st.title("📊 Trading-Runde")
    st.caption("Mehrere Grok-Agenten diskutieren mit dir. Keine Finanzberatung.")

    if "chat" not in st.session_state:
        # {"name", "avatar", "text", "kind", "sources"} – kind ist msg, error oder notice.
        st.session_state.chat = []
    if "market_nonce" not in st.session_state:
        st.session_state.market_nonce = 0
    if "market" not in st.session_state:
        st.session_state.market = None

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
            st.caption("Jede Suche kostet extra API-Guthaben. Der Moderator recherchiert nicht.")
        st.divider()
        if st.button("Chat leeren"):
            st.session_state.chat = []
            st.rerun()

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
            st.markdown(f"**{message['name']}**  \n{present(message)}")

    # Steuerung
    c1, c2 = st.columns(2)
    more = c1.button(
        "🔁 Weiter diskutieren",
        disabled=not st.session_state.chat or not active,
        width="stretch",
    )
    summary = c2.button(
        "🧭 Fazit ziehen",
        disabled=not st.session_state.chat,
        width="stretch",
    )

    prompt = st.chat_input(
        "Deine Nachricht an die Runde, z. B. 'Lohnt sich ein Einstieg bei Nvidia nach den Zahlen?'"
    )

    if prompt:
        spoken = entry(who, "🧑", prompt, "msg")
        st.session_state.chat.append(spoken)
        with st.chat_message(who, avatar="🧑"):
            st.markdown(f"**{who}**  \n{present(spoken)}")
        run_round(active, use_web=use_web, use_x=use_x)
    elif more:
        run_round(active, use_web=use_web, use_x=use_x)
    elif summary:
        run_round([MODERATOR], use_web=use_web, use_x=use_x)

    if prompt or more or summary:
        st.rerun()  # Knöpfe mit neuem Stand neu zeichnen


if __name__ == "__main__":
    main()
