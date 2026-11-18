"""Offline-Checks für Kurse und Agenten-Prompts. Kein Netz, kein API-Key."""

import io
import json
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from email.message import Message

from agents import AGENTS, MODERATOR, instructions
from app import present, tool_args, transcript
from market import (
    MarketUnavailable,
    fetch_market,
    fmt_number,
    fmt_pct,
    format_age,
    format_context,
    normalize_symbol,
    parse_chart,
    sample_market,
)
import market


class MarketTests(unittest.TestCase):
    def test_aliases_and_rejects_garbage(self):
        self.assertEqual(normalize_symbol(" btc "), "BTC-USD")
        self.assertEqual(normalize_symbol("eth/usd"), "ETH-USD")
        self.assertEqual(normalize_symbol("dax"), "^GDAXI")
        self.assertEqual(normalize_symbol("sap.de"), "SAP.DE")
        self.assertFalse(sample_market("???", "3mo")["ok"])
        self.assertFalse(sample_market("   ", "3mo")["ok"])

    def test_sample_is_deterministic_and_offline(self):
        def boom(*_args, **_kwargs):
            raise AssertionError("sample_market darf kein Netz nutzen")

        original = urllib.request.urlopen
        urllib.request.urlopen = boom
        try:
            first = sample_market("NVDA", "3mo", today=date(2026, 11, 18))
            second = sample_market("nvda", "3mo", today=date(2026, 11, 18))
        finally:
            urllib.request.urlopen = original
        self.assertTrue(first["ok"])
        self.assertTrue(first["mock"])
        self.assertEqual(first, second)
        self.assertEqual(len(first["closes"]), 66)
        self.assertEqual(first["dates"][-1], "2026-11-18")
        self.assertEqual(sample_market("BTC", "5d", today=date(2026, 11, 18))["symbol"], "BTC-USD")

    def test_context_marks_sample_data_and_failures(self):
        snap = sample_market("SAP.DE", "5d", today=date(2026, 11, 18))
        text = format_context(snap)
        self.assertIn("SAP.DE", text)
        self.assertIn("Beispieldaten", text)
        self.assertIn("Erfinde keine abweichenden Kurse", text)
        self.assertIn("nur reguläre Handelszeit", text)
        btc = format_context(sample_market("BTC", "5d", today=date(2026, 11, 18)))
        self.assertNotIn("00:00 UTC", btc)
        missing = format_context(sample_market(" ", "3mo"))
        self.assertIn("Erfinde keine Kurse", missing)
        self.assertNotIn("Letzter Kurs", missing)

    def test_parse_chart_scales_london_pence_and_missing_symbol(self):
        payload = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "symbol": "VOD.L",
                            "shortName": "Vodafone                 I",
                            "longName": "Vodafone Group",
                            "currency": "GBp",
                            "regularMarketPrice": 80_00,
                            "regularMarketTime": 1_795_035_600,
                            "gmtoffset": 0,
                            "exchangeTimezoneName": "Europe/London",
                        },
                        "timestamp": [1_794_900_000, 1_794_986_400],
                        "indicators": {
                            "quote": [
                                {
                                    "close": [7000, 7500],
                                    "high": [7100, 7600],
                                    "low": [6900, 7400],
                                    "volume": [100, 200],
                                }
                            ]
                        },
                    }
                ],
                "error": None,
            }
        }
        snap = parse_chart("VOD.L", "5d", payload)
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["name"], "Vodafone Group")
        self.assertEqual(snap["currency"], "GBP")
        self.assertAlmostEqual(snap["price"], 80.0)
        self.assertEqual(len(snap["closes"]), 2)
        self.assertFalse(snap["intraday"])
        self.assertAlmostEqual(snap["closes"][-1], 80.0)
        self.assertAlmostEqual(snap["prev_close"], 70.0)
        missing = parse_chart("NOPE", "3mo", {"chart": {"result": None, "error": {"code": "Not Found"}}})
        self.assertFalse(missing["ok"])
        self.assertIn("NOPE", missing["error"])

    def test_null_today_close_appends_intraday_bar(self):
        yesterday = 1_794_934_800  # 2026-11-17 18:00 Europe/Berlin
        today_bar = 1_794_988_800  # 2026-11-18 09:00, Schluss null
        quote = 1_794_994_200  # 2026-11-18 10:30
        cases = (
            ("^GDAXI", "regularMarketDayHigh", "regularMarketDayLow", "regularMarketVolume"),
            ("SAP.DE", "dayHigh", "dayLow", "volume"),
        )
        for symbol, high_key, low_key, vol_key in cases:
            with self.subTest(symbol=symbol):
                payload = {
                    "chart": {
                        "result": [
                            {
                                "meta": {
                                    "symbol": symbol,
                                    "shortName": symbol,
                                    "currency": "EUR",
                                    "regularMarketPrice": 19123.5,
                                    "regularMarketTime": quote,
                                    "gmtoffset": 3600,
                                    "exchangeTimezoneName": "Europe/Berlin",
                                    "marketState": "REGULAR",
                                    "instrumentType": "INDEX" if symbol.startswith("^") else "EQUITY",
                                    high_key: 19200.0,
                                    low_key: 19010.0,
                                    vol_key: 12_000_000,
                                },
                                "timestamp": [yesterday, today_bar],
                                "indicators": {
                                    "quote": [
                                        {
                                            "close": [19000.0, None],
                                            "high": [19080.0, None],
                                            "low": [18940.0, None],
                                            "volume": [80_000_000, None],
                                        }
                                    ]
                                },
                            }
                        ],
                        "error": None,
                    }
                }
                snap = parse_chart(symbol, "5d", payload)
                self.assertTrue(snap["ok"])
                self.assertTrue(snap["intraday"])
                self.assertEqual(snap["dates"], ["2026-11-17", "2026-11-18"])
                self.assertAlmostEqual(snap["closes"][-1], 19123.5)
                self.assertAlmostEqual(snap["prev_close"], 19000.0)
                self.assertAlmostEqual(snap["high"], 19200.0)
                self.assertAlmostEqual(snap["low"], 18940.0)
                self.assertEqual(snap["volume"], 12_000_000)
                self.assertEqual(snap["market_state"], "open")
                text = format_context(snap)
                self.assertIn("18.11. 19.123,50 (intraday)", text)
                self.assertIn("nur reguläre Handelszeit", text)
                self.assertIn("Marktstatus: geöffnet", text)
                self.assertIn("Datenalter:", text)
                self.assertNotIn("00:00 UTC", text)

    def test_no_intraday_bar_without_live_price(self):
        payload = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "symbol": "SAP.DE",
                            "currency": "EUR",
                            "regularMarketTime": 1_794_994_200,
                            "gmtoffset": 3600,
                        },
                        "timestamp": [1_794_934_800, 1_794_988_800],
                        "indicators": {"quote": [{"close": [200.0, None], "high": [202.0, None], "low": [198.0, None]}]},
                    }
                ],
                "error": None,
            }
        }
        snap = parse_chart("SAP.DE", "5d", payload)
        self.assertTrue(snap["ok"])
        self.assertFalse(snap["intraday"])
        self.assertEqual(snap["dates"], ["2026-11-17"])
        self.assertAlmostEqual(snap["price"], 200.0)

    def test_market_state_and_crypto_change_note(self):
        def payload(meta):
            return {
                "chart": {
                    "result": [
                        {
                            "meta": meta,
                            "timestamp": [1_794_934_800, 1_794_994_200],
                            "indicators": {
                                "quote": [
                                    {
                                        "close": [100.0, 110.0],
                                        "high": [101.0, 111.0],
                                        "low": [99.0, 109.0],
                                        "volume": [10, 11],
                                    }
                                ]
                            },
                        }
                    ],
                    "error": None,
                }
            }

        base = {
            "symbol": "BTC-USD",
            "currency": "USD",
            "regularMarketPrice": 110.0,
            "regularMarketTime": 1_794_994_200,
            "gmtoffset": 0,
            "instrumentType": "CRYPTOCURRENCY",
            "currentTradingPeriod": {
                "pre": {"start": 0, "end": 1},
                "regular": {"start": 1, "end": 2},
                "post": {"start": 0, "end": 4_102_444_800},
            },
        }
        closed = parse_chart("BTC-USD", "5d", payload({**base, "marketState": "CLOSED"}))
        self.assertEqual(closed["market_state"], "closed")
        self.assertTrue(closed["crypto"])
        text = format_context(closed)
        self.assertIn("seit 00:00 UTC", text)
        self.assertIn("Marktstatus: geschlossen", text)
        self.assertNotIn("reguläre Handelszeit", text)

        post = parse_chart("BTC-USD", "5d", payload({**base, "marketState": ""}))
        self.assertEqual(post["market_state"], "post")
        pre = parse_chart("SAP.DE", "5d", payload({
            **base,
            "symbol": "SAP.DE",
            "currency": "EUR",
            "instrumentType": "EQUITY",
            "marketState": "PREPRE",
        }))
        self.assertEqual(pre["market_state"], "pre")
        self.assertFalse(pre["crypto"])
        self.assertIn("nur reguläre Handelszeit", format_context(pre))

    def test_format_age_steps(self):
        now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(format_age(None, now=now), "")
        self.assertEqual(format_age(now.timestamp() - 30, now=now), "unter 1 Minute")
        self.assertEqual(format_age(now.timestamp() - 60, now=now), "1 Minute")
        self.assertEqual(format_age(now.timestamp() - 120, now=now), "2 Minuten")
        self.assertEqual(format_age(now.timestamp() - 3600, now=now), "1 Stunde")
        self.assertEqual(format_age(now.timestamp() - 3 * 3600, now=now), "3 Stunden")
        self.assertEqual(format_age(now.timestamp() - 86400, now=now), "1 Tag")
        self.assertEqual(format_age(now.timestamp() - 2 * 86400, now=now), "2 Tage")

    def test_rate_limit_is_not_parsed_and_retries_once(self):
        calls = {"n": 0}
        body = b'{"chart": {"result": null, "error": {"code": "Not Found"}}}'

        def boom(request, timeout=15):
            calls["n"] += 1
            hdrs = Message()
            raise urllib.error.HTTPError(request.full_url, 429, "rate", hdrs, io.BytesIO(body))

        original = urllib.request.urlopen
        pause = market._RETRY_PAUSE
        urllib.request.urlopen = boom
        market._RETRY_PAUSE = 0
        try:
            with self.assertRaises(MarketUnavailable):
                fetch_market("NVDA", "5d")
        finally:
            urllib.request.urlopen = original
            market._RETRY_PAUSE = pause
        # Zwei Hosts, davon der erste genau einmal wiederholt.
        self.assertEqual(calls["n"], 3)

    def test_server_error_retries_once_then_can_succeed(self):
        calls = {"n": 0}
        good = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "symbol": "NVDA",
                            "currency": "USD",
                            "regularMarketPrice": 140.0,
                            "regularMarketTime": 1_794_994_200,
                            "gmtoffset": 0,
                            "marketState": "POST",
                        },
                        "timestamp": [1_794_934_800, 1_794_994_200],
                        "indicators": {
                            "quote": [
                                {
                                    "close": [130.0, 139.0],
                                    "high": [131.0, 141.0],
                                    "low": [129.0, 138.0],
                                    "volume": [10, 12],
                                }
                            ]
                        },
                    }
                ],
                "error": None,
            }
        }

        class _Resp:
            status = 200

            def read(self):
                return json.dumps(good).encode()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def fake(request, timeout=15):
            calls["n"] += 1
            if calls["n"] == 1:
                hdrs = Message()
                raw = b'{"chart": {"result": null, "error": {"code": "boom"}}}'
                raise urllib.error.HTTPError(request.full_url, 503, "down", hdrs, io.BytesIO(raw))
            return _Resp()

        original = urllib.request.urlopen
        pause = market._RETRY_PAUSE
        urllib.request.urlopen = fake
        market._RETRY_PAUSE = 0
        try:
            snap = fetch_market("NVDA", "5d")
        finally:
            urllib.request.urlopen = original
            market._RETRY_PAUSE = pause
        self.assertTrue(snap["ok"])
        self.assertEqual(calls["n"], 2)
        self.assertEqual(snap["market_state"], "post")
        self.assertAlmostEqual(snap["price"], 140.0)

    def test_not_found_reads_body_without_retry(self):
        calls = {"n": 0}

        def boom(request, timeout=15):
            calls["n"] += 1
            hdrs = Message()
            raw = b'{"chart": {"result": null, "error": {"code": "Not Found"}}}'
            raise urllib.error.HTTPError(request.full_url, 404, "missing", hdrs, io.BytesIO(raw))

        original = urllib.request.urlopen
        urllib.request.urlopen = boom
        try:
            snap = fetch_market("NOPE", "5d")
        finally:
            urllib.request.urlopen = original
        self.assertFalse(snap["ok"])
        self.assertIn("NOPE", snap["error"])
        self.assertEqual(calls["n"], 1)

    def test_invalid_symbol_does_not_download(self):
        def boom(*_args, **_kwargs):
            raise AssertionError("ungültige Symbole dürfen Yahoo nicht aufrufen")

        original = urllib.request.urlopen
        urllib.request.urlopen = boom
        try:
            snap = fetch_market("???", "3mo")
        finally:
            urllib.request.urlopen = original
        self.assertFalse(snap["ok"])

    def test_network_error_raises(self):
        def boom(*_args, **_kwargs):
            raise urllib.error.URLError("timed out")

        original = urllib.request.urlopen
        urllib.request.urlopen = boom
        try:
            with self.assertRaises(MarketUnavailable):
                fetch_market("NVDA", "5d")
        finally:
            urllib.request.urlopen = original

    def test_number_format(self):
        self.assertEqual(fmt_number(1234.5), "1.234,50")
        self.assertEqual(fmt_number(-0.5, 2), "-0,50")
        self.assertEqual(fmt_pct(1.2), "+1,20 %")
        self.assertEqual(fmt_pct(-3), "-3,00 %")


class AgentTests(unittest.TestCase):
    def test_new_specialists_and_order(self):
        names = [agent["name"] for agent in AGENTS]
        self.assertEqual(
            names,
            ["Chartist", "Makro", "Krypto", "Optionen", "Flow", "Katalysator", "Risiko", "Skeptiker"],
        )
        self.assertEqual(MODERATOR["name"], "Moderator")
        self.assertEqual(len({agent["avatar"] for agent in AGENTS}), len(AGENTS))

    def test_search_toggle_is_honest(self):
        chartist = AGENTS[0]
        quiet = instructions(chartist, "Daniel", use_web=False, use_x=False)
        self.assertIn("Daniel", quiet)
        self.assertIn("Websuche und X-Suche sind in dieser Runde aus", quiet)
        self.assertNotIn("Du hast Websuche", quiet)
        web_only = instructions(chartist, "Ada", use_web=True, use_x=False)
        self.assertIn("Du hast Websuche:", web_only)
        self.assertNotIn("X-Suche:", web_only)
        self.assertIn("Marktdaten haben Vorrang", web_only)

    def test_moderator_has_no_research_instruction(self):
        text = instructions(MODERATOR, "Daniel", use_web=True, use_x=True)
        self.assertIn("Moderator", text)
        self.assertNotIn("Websuche", text)
        self.assertNotIn("X-Suche", text)
        self.assertNotIn("Recherchiere", text)
        self.assertEqual(tool_args(MODERATOR, use_web=True, use_x=True), {"tools": []})
        self.assertEqual(
            tool_args(AGENTS[0], use_web=True, use_x=False),
            {"tools": [{"type": "web_search"}]},
        )
        self.assertEqual(tool_args(AGENTS[0], use_web=False, use_x=False), {})


class TranscriptTests(unittest.TestCase):
    def test_only_messages_reach_the_model(self):
        messages = [
            {"name": "Daniel", "kind": "msg", "text": "Was ist mit Nvidia?"},
            {
                "name": "Chartist",
                "kind": "msg",
                "text": "Der Trend ist intakt.",
                "sources": ["https://example.com/news"],
            },
            {"name": "Makro", "kind": "error", "text": "⚠️ Fehler bei der Grok-Anfrage: 401"},
            {"name": "Krypto", "kind": "notice", "text": "⚠️ Keine Antwort vom Modell erhalten."},
            {
                "name": "Risiko",
                "kind": "msg",
                "text": "Stop zu eng.\n\n*Quellen:* [1](https://example.com/secret)",
            },
        ]
        text = transcript(messages)
        self.assertIn("Daniel: Was ist mit Nvidia?", text)
        self.assertIn("Chartist: Der Trend ist intakt.", text)
        self.assertIn("Risiko: Stop zu eng.", text)
        self.assertNotIn("Fehler bei der Grok-Anfrage", text)
        self.assertNotIn("Keine Antwort vom Modell", text)
        self.assertNotIn("Quellen", text)
        self.assertNotIn("example.com", text)
        shown = present(messages[1])
        self.assertIn("Der Trend ist intakt.", shown)
        self.assertIn("*Quellen:*", shown)
        self.assertIn("https://example.com/news", shown)

    def test_truncation_ignores_errors(self):
        messages = [
            {"name": "Daniel", "kind": "msg", "text": "A" * 80},
            {"name": "Makro", "kind": "error", "text": "B" * 80},
            {"name": "Chartist", "kind": "msg", "text": "C" * 80},
        ]
        text = transcript(messages, max_chars=100)
        self.assertIn("Chartist:", text)
        self.assertNotIn("Makro:", text)
        self.assertNotIn("B" * 20, text)
        self.assertIn("gekürzt", text)


if __name__ == "__main__":
    unittest.main()
