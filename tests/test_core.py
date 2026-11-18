"""Offline-Checks für Kurse und Agenten-Prompts. Kein Netz, kein API-Key."""

import unittest
import urllib.error
import urllib.request
from datetime import date

from agents import AGENTS, MODERATOR, instructions
from market import (
    MarketUnavailable,
    fetch_market,
    fmt_number,
    fmt_pct,
    format_context,
    normalize_symbol,
    parse_chart,
    sample_market,
)


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
        self.assertAlmostEqual(snap["closes"][-1], 80.0)
        self.assertAlmostEqual(snap["prev_close"], 70.0)
        missing = parse_chart("NOPE", "3mo", {"chart": {"result": None, "error": {"code": "Not Found"}}})
        self.assertFalse(missing["ok"])
        self.assertIn("NOPE", missing["error"])

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


if __name__ == "__main__":
    unittest.main()
