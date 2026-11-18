"""Kursdaten ohne API-Key.

Live-Kurse kommen von der Yahoo-Finance-Chart-Schnittstelle (dieselbe Quelle wie
yfinance). Yahoo antwortet auf nackte Skript-Anfragen oft mit HTTP 429, deshalb
geht ein normaler Browser-Kopf mit. Mit MOCK=1 ruft die App nur sample_market
auf und braucht kein Netz.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

CUSTOM = "__custom__"

PRESETS: list[tuple[str, str]] = [
    ("NVDA", "NVDA · Nvidia"),
    ("AAPL", "AAPL · Apple"),
    ("MSFT", "MSFT · Microsoft"),
    ("TSLA", "TSLA · Tesla"),
    ("SPY", "SPY · S&P 500"),
    ("QQQ", "QQQ · Nasdaq 100"),
    ("^GDAXI", "^GDAXI · DAX"),
    ("SAP.DE", "SAP.DE · SAP"),
    ("BTC-USD", "BTC-USD · Bitcoin"),
    ("ETH-USD", "ETH-USD · Ethereum"),
    ("SOL-USD", "SOL-USD · Solana"),
    (CUSTOM, "Anderes Symbol …"),
]

PERIODS: dict[str, str] = {
    "5d": "5 Tage",
    "1mo": "1 Monat",
    "3mo": "3 Monate",
    "6mo": "6 Monate",
    "1y": "1 Jahr",
    "2y": "2 Jahre",
}

# Kurze Eingaben, die Trader tippen, auf das Yahoo-Kürzel legen.
# SOL/ETH/BTC sind hier Krypto; eine Aktie mit demselben Kürzel bitte voll ausschreiben.
ALIASES = {
    "BITCOIN": "BTC-USD",
    "BTC": "BTC-USD",
    "BTCUSD": "BTC-USD",
    "ETHEREUM": "ETH-USD",
    "ETH": "ETH-USD",
    "ETHUSD": "ETH-USD",
    "SOLANA": "SOL-USD",
    "SOL": "SOL-USD",
    "SOLUSD": "SOL-USD",
    "DOGE": "DOGE-USD",
    "DOGEUSD": "DOGE-USD",
    "XRP": "XRP-USD",
    "XRPUSD": "XRP-USD",
    "ADA": "ADA-USD",
    "ADAUSD": "ADA-USD",
    "DAX": "^GDAXI",
    "GDAXI": "^GDAXI",
    "SPX": "^GSPC",
    "SP500": "^GSPC",
    "S&P500": "^GSPC",
    "NASDAQ": "^IXIC",
    "NDX": "^NDX",
}

_SYMBOL_RE = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=]{0,24}$")
_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_SAMPLE_BASE = {
    "NVDA": 140.0,
    "AAPL": 230.0,
    "MSFT": 420.0,
    "TSLA": 250.0,
    "SPY": 560.0,
    "QQQ": 490.0,
    "^GDAXI": 19000.0,
    "^GSPC": 5600.0,
    "^NDX": 20000.0,
    "^IXIC": 18000.0,
    "SAP.DE": 220.0,
    "BTC-USD": 95000.0,
    "ETH-USD": 3200.0,
    "SOL-USD": 180.0,
}
_SAMPLE_BARS = {"5d": 5, "1mo": 22, "3mo": 66, "6mo": 132, "1y": 252, "2y": 504}
_INDEX_CCY = {"^GDAXI": "EUR", "^STOXX50E": "EUR", "^FTSE": "GBP", "^N225": "JPY", "^FCHI": "EUR"}


class MarketUnavailable(Exception):
    """Yahoo Finance oder das Netz ist gerade nicht erreichbar."""


def normalize_symbol(raw: str) -> str:
    text = (raw or "").strip().upper().replace("/", "-")
    text = re.sub(r"\s+", "", text)
    if not text:
        return ""
    return ALIASES.get(text, text)


def valid_symbol(symbol: str) -> bool:
    return bool(_SYMBOL_RE.match(symbol or ""))


def fmt_number(value: float, digits: int = 2) -> str:
    sign = "-" if value < 0 else ""
    raw = f"{abs(value):,.{digits}f}"
    if "." not in raw:
        return sign + raw.replace(",", ".")
    whole, frac = raw.split(".")
    return f"{sign}{whole.replace(',', '.')},{frac}"


def fmt_pct(value: float) -> str:
    text = fmt_number(value)
    if value > 0:
        return f"+{text} %"
    return f"{text} %"


def fmt_volume(value: float) -> str:
    if value >= 1_000_000_000:
        return f"{fmt_number(value / 1_000_000_000, 1)} Mrd."
    if value >= 1_000_000:
        return f"{fmt_number(value / 1_000_000, 1)} Mio."
    if value >= 1_000:
        return f"{fmt_number(value / 1_000, 1)} Tsd."
    return fmt_number(value, 0)


def price_digits(value: float) -> int:
    magnitude = abs(value)
    if magnitude >= 1:
        return 2
    if magnitude >= 0.01:
        return 4
    return 6


def failed(symbol: str, error: str, *, mock: bool = False) -> dict:
    return {
        "ok": False,
        "mock": mock,
        "symbol": symbol,
        "name": symbol,
        "currency": "",
        "price": None,
        "prev_close": None,
        "change": None,
        "change_pct": None,
        "period": "",
        "period_label": "",
        "high": None,
        "low": None,
        "period_change_pct": None,
        "volume": None,
        "avg_volume": None,
        "as_of": "",
        "source": "Beispieldaten (MOCK)" if mock else "Yahoo Finance",
        "dates": [],
        "closes": [],
        "error": error,
    }


def _invalid_message(symbol: str) -> str:
    shown = symbol or "leer"
    return (
        f'Kein gültiges Symbol: "{shown}". '
        "Beispiele: NVDA, SAP.DE, BTC-USD, ^GDAXI."
    )


def _missing_message(symbol: str) -> str:
    return (
        f'Kein Kurs für "{symbol}". '
        "Symbol prüfen (Aktien z. B. NVDA oder SAP.DE, Krypto z. B. BTC-USD)."
    )


def guess_currency(symbol: str) -> str:
    if symbol in _INDEX_CCY:
        return _INDEX_CCY[symbol]
    if symbol.endswith("-USD"):
        return "USD"
    if symbol.endswith((".DE", ".F", ".MU", ".PA", ".AS", ".MI", ".BR")):
        return "EUR"
    if symbol.endswith(".SW"):
        return "CHF"
    if symbol.endswith((".L", ".IL")):
        return "GBP"
    if symbol.endswith(".TO"):
        return "CAD"
    return "USD"


def _squash(value) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _instrument_name(meta: dict, symbol: str) -> str:
    # shortName ist bei manchen Werten mit Leerzeichen aufgefüllt ("DAX      P").
    # longName ist dann der lesbare Name.
    for raw in (meta.get("longName"), meta.get("shortName")):
        text = _squash(raw)
        if text:
            return text
    return _preset_name(symbol)


def _preset_name(symbol: str) -> str:
    for sym, label in PRESETS:
        if sym == symbol and "·" in label:
            return label.split("·", 1)[1].strip()
    return symbol


def format_context(snapshot: dict | None) -> str:
    """Kurzer Block für den Prompt. Enthält keine erfundenen Live-Kurse."""
    if not snapshot:
        return "Marktdaten: kein Symbol geladen. Erfinde keine Kurse."
    if not snapshot.get("ok"):
        detail = (snapshot.get("error") or "keine Kursdaten").rstrip(".")
        symbol = snapshot.get("symbol") or "?"
        return (
            f'Marktdaten: für "{symbol}" liegen keine Kurse vor ({detail}). '
            "Erfinde keine Kurse."
        )
    digits = price_digits(float(snapshot["price"]))
    lines = [
        f"Marktdaten ({snapshot['source']}, Tagesdaten, Stand {snapshot['as_of']}):",
        f"{snapshot['name']} ({snapshot['symbol']}), Währung {snapshot['currency'] or '—'}",
        f"Letzter Kurs: {fmt_number(snapshot['price'], digits)}",
        (
            f"Vorheriger Schluss: {fmt_number(snapshot['prev_close'], digits)} "
            f"(Veränderung {fmt_pct(snapshot['change_pct'])})"
        ),
        (
            f"Zeitraum {snapshot['period_label']}: Hoch {fmt_number(snapshot['high'], digits)} · "
            f"Tief {fmt_number(snapshot['low'], digits)} · "
            f"Veränderung {fmt_pct(snapshot['period_change_pct'])}"
        ),
    ]
    if snapshot.get("volume"):
        avg = snapshot.get("avg_volume")
        vol = f"Volumen letzter Tag: {fmt_volume(snapshot['volume'])}"
        if avg:
            vol += f" · Schnitt: {fmt_volume(avg)}"
        lines.append(vol)
    tail = list(zip(snapshot["dates"][-8:], snapshot["closes"][-8:]))
    recent = " · ".join(
        f"{datetime.strptime(day, '%Y-%m-%d'):%d.%m.} {fmt_number(close, price_digits(close))}"
        for day, close in tail
    )
    lines.append(f"Letzte Schlüsse: {recent}")
    lines.append("Das ist das in der App gewählte Symbol. Wenn die Frage ein anderes meint, sag das klar.")
    if snapshot.get("mock"):
        lines.append(
            "ACHTUNG: Das sind synthetische Beispieldaten aus dem Testmodus, keine echten Kurse. "
            "Erfinde keine abweichenden Kurse."
        )
    else:
        lines.append("Diese Zahlen sind die Grundlage für Kursangaben. Erfinde keine abweichenden Kurse.")
    return "\n".join(lines)


def sample_market(symbol: str, period: str, *, today: date | None = None) -> dict:
    """Stabiler Beispielchart. Deterministisch pro Symbol und Zeitraum, ohne Netz."""
    sym = normalize_symbol(symbol)
    period = period if period in PERIODS else "3mo"
    if not valid_symbol(sym):
        return failed(sym or (symbol or "").strip(), _invalid_message(symbol), mock=True)
    crypto = sym.endswith("-USD")
    bars = _SAMPLE_BARS[period]
    if crypto:
        bars = {"5d": 5, "1mo": 30, "3mo": 90, "6mo": 182, "1y": 365, "2y": 730}[period]
    end = today or date.today()
    days = _calendar_days(end, bars) if crypto else _business_days(end, bars)
    seed = int(hashlib.sha256(f"{sym}:{period}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    base = _SAMPLE_BASE.get(sym)
    if base is None:
        base = 20 + (seed % 4000) / 10
    vol = 0.028 if crypto else (0.008 if sym.startswith("^") else 0.014)
    price = base
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    volumes: list[float] = []
    for _ in range(bars):
        price = max(price * (1 + rng.gauss(0.0004, vol)), 0.0001)
        digits = price_digits(price)
        close = round(price, digits)
        spread = abs(rng.gauss(0, vol / 2))
        highs.append(round(close * (1 + spread), digits))
        lows.append(round(max(close * (1 - spread), 0.0001), digits))
        closes.append(close)
        volumes.append(base * (50_000 if crypto else 80_000) * (0.4 + rng.random()))
    last = closes[-1]
    prev = closes[-2] if len(closes) > 1 else last
    first = closes[0]
    return {
        "ok": True,
        "mock": True,
        "symbol": sym,
        "name": _preset_name(sym),
        "currency": guess_currency(sym),
        "price": last,
        "prev_close": prev,
        "change": last - prev,
        "change_pct": ((last / prev) - 1) * 100 if prev else 0.0,
        "period": period,
        "period_label": PERIODS[period],
        "high": max(highs),
        "low": min(lows),
        "period_change_pct": ((last / first) - 1) * 100 if first else 0.0,
        "volume": volumes[-1],
        "avg_volume": sum(volumes) / len(volumes),
        "as_of": f"{days[-1]:%d.%m.%Y} (Beispieldaten)",
        "source": "Beispieldaten (MOCK)",
        "dates": [day.isoformat() for day in days],
        "closes": closes,
        "error": "",
    }


def fetch_market(symbol: str, period: str) -> dict:
    sym = normalize_symbol(symbol)
    period = period if period in PERIODS else "3mo"
    if not valid_symbol(sym):
        return failed(sym or (symbol or "").strip(), _invalid_message(symbol))
    payload = _download_chart(sym, period)
    return parse_chart(sym, period, payload)


def parse_chart(symbol: str, period: str, payload: dict) -> dict:
    period = period if period in PERIODS else "3mo"
    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict):
        raise MarketUnavailable("Yahoo Finance hat keine lesbare Antwort geliefert.")
    if chart.get("error") or not chart.get("result"):
        return failed(symbol, _missing_message(symbol))
    result = chart["result"][0] or {}
    meta = result.get("meta") or {}
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    stamps = result.get("timestamp") or []
    closes = quote.get("close") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    volumes = quote.get("volume") or []
    currency = meta.get("currency") or guess_currency(symbol)
    scale = 0.01 if currency == "GBp" else 1.0
    if currency == "GBp":
        currency = "GBP"

    points: list[tuple[str, float, float, float, float | None]] = []
    offset = int(meta.get("gmtoffset") or 0)
    tz = timezone(timedelta(seconds=offset))
    for i, stamp in enumerate(stamps):
        raw_close = closes[i] if i < len(closes) else None
        if raw_close is None or raw_close <= 0:
            continue
        close = float(raw_close) * scale
        raw_high = highs[i] if i < len(highs) and highs[i] is not None else raw_close
        raw_low = lows[i] if i < len(lows) and lows[i] is not None else raw_close
        raw_vol = volumes[i] if i < len(volumes) else None
        day = datetime.fromtimestamp(int(stamp), tz).date().isoformat()
        points.append(
            (
                day,
                close,
                float(raw_high) * scale,
                float(raw_low) * scale,
                None if raw_vol is None else float(raw_vol),
            )
        )
    if not points:
        return failed(symbol, _missing_message(symbol))

    # Yahoo legt manchmal mehrere Stempel auf denselben Handelstag. Der letzte Schluss gewinnt.
    merged: dict[str, tuple[float, float, float, float | None]] = {}
    for day, close, high, low, vol in points:
        prev = merged.get(day)
        if prev is None:
            merged[day] = (close, high, low, vol)
        else:
            merged[day] = (
                close,
                max(prev[1], high),
                min(prev[2], low),
                vol if vol is not None else prev[3],
            )
    days = list(merged)
    series = [merged[day] for day in days]
    live = meta.get("regularMarketPrice")
    last_close = series[-1][0]
    price = float(live) * scale if isinstance(live, (int, float)) and live > 0 else last_close
    session_day = _session_day(meta, tz, days[-1])
    # Liegt der Live-Kurs auf einem neueren Tag als die letzte Kerze, ist der
    # letzte Schluss der Vortag. Sonst ist der Vortag die Kerze davor.
    if days[-1] == session_day and len(series) > 1:
        prev_close = series[-2][0]
        series[-1] = (price, max(series[-1][1], price), min(series[-1][2], price), series[-1][3])
    else:
        prev_close = last_close
    first = series[0][0]
    as_of = _as_of(meta, tz, days[-1])
    name = _instrument_name(meta, symbol)
    vols = [item[3] for item in series if item[3]]
    return {
        "ok": True,
        "mock": False,
        "symbol": meta.get("symbol") or symbol,
        "name": name,
        "currency": currency,
        "price": price,
        "prev_close": prev_close,
        "change": price - prev_close,
        "change_pct": ((price / prev_close) - 1) * 100 if prev_close else 0.0,
        "period": period,
        "period_label": PERIODS[period],
        "high": max(item[1] for item in series),
        "low": min(item[2] for item in series),
        "period_change_pct": ((price / first) - 1) * 100 if first else 0.0,
        "volume": vols[-1] if vols else None,
        "avg_volume": (sum(vols) / len(vols)) if vols else None,
        "as_of": as_of,
        "source": "Yahoo Finance",
        "dates": days,
        "closes": [item[0] for item in series],
        "error": "",
    }


def _session_day(meta: dict, tz: timezone, fallback_day: str) -> str:
    stamp = meta.get("regularMarketTime")
    if isinstance(stamp, (int, float)) and stamp > 0:
        return datetime.fromtimestamp(int(stamp), tz).date().isoformat()
    return fallback_day


def _as_of(meta: dict, tz: timezone, fallback_day: str) -> str:
    stamp = meta.get("regularMarketTime")
    zone = meta.get("exchangeTimezoneName") or "UTC"
    if isinstance(stamp, (int, float)) and stamp > 0:
        moment = datetime.fromtimestamp(int(stamp), tz)
        return f"{moment:%d.%m.%Y %H:%M} ({zone})"
    parsed = datetime.strptime(fallback_day, "%Y-%m-%d")
    return f"{parsed:%d.%m.%Y} ({zone})"


def _download_chart(symbol: str, period: str) -> dict:
    encoded = urllib.parse.quote(symbol, safe="")
    query = urllib.parse.urlencode({"range": period, "interval": "1d", "includePrePost": "false"})
    last_error: Exception | None = None
    for host in _HOSTS:
        url = f"https://{host}/v8/finance/chart/{encoded}?{query}"
        request = urllib.request.Request(url, headers=_HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            last_error = exc
            body = exc.read() if exc.fp else b""
            if body:
                try:
                    return json.loads(body.decode())
                except json.JSONDecodeError:
                    pass
            if exc.code in (404, 400, 422):
                return {"chart": {"result": None, "error": {"code": str(exc.code)}}}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = exc
    detail = type(last_error).__name__ if last_error else "unbekannt"
    raise MarketUnavailable(
        f"Kursdaten gerade nicht erreichbar ({detail}). Die Runde kann ohne Live-Kurse weiterreden."
    )


def _business_days(end: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return days


def _calendar_days(end: date, count: int) -> list[date]:
    start = end - timedelta(days=count - 1)
    return [start + timedelta(days=offset) for offset in range(count)]
