"""Tests hors réseau : normalisation, percentiles, jointures, filtres et formule."""
import unittest

from modelpicker.match import ModelIndex, normalize, strip_variant
from modelpicker.scoring import PRIOR, percentiles, recommend


def model(mid, name, pin, pout, ctx=200_000, aa=None, hf=None, canon=None, inputs=("text",), params=("tools",)):
    return {"id": mid, "canonical_slug": canon or mid, "name": name, "created": 1, "context_length": ctx,
            "price_in": pin / 1e6, "price_out": pout / 1e6, "input_modalities": list(inputs),
            "output_modalities": ["text"], "supported_parameters": list(params), "hugging_face_id": hf,
            "expiration_date": None, "artificial_analysis": aa}


MODELS = {"source_date": "2026-09-17", "data": [
    model("acme/big-5", "Acme: Big 5", 5, 25, ctx=1_000_000,
          aa={"intelligence_index": 70, "coding_index": 70, "agentic_index": 70}, canon="acme/big-5-20260101"),
    model("acme/big-5:batch", "Acme: Big 5 (batch)", 2.5, 12.5),
    model("acme/small-5", "Acme: Small 5", 0.1, 0.4, aa={"intelligence_index": 50, "coding_index": 50, "agentic_index": 50}),
    model("open/lite-2", "Open: Lite 2", 0.05, 0.1, hf="open/lite-2",
          aa={"intelligence_index": 30, "coding_index": 30, "agentic_index": 30}),
    model("open/lite-2:free", "Open: Lite 2 (free)", 0, 0),
    model("~acme/big-latest", "Acme: Big Latest", 5, 25),
    model("acme/unscored", "Acme: Unscored", 1, 1),
]}
USAGE = [{"source_date": "2026-09-16", "data": {
    "acme/big-5-20260101": {"prompt": 900, "completion": 100, "requests": 10, "tool_calls": 0},
    "open/lite-2": {"prompt": 5000, "completion": 0, "requests": 50, "tool_calls": 0},
    "acme/unscored": {"prompt": 9000, "completion": 1000, "requests": 99, "tool_calls": 0},
}}]
LMARENA = {"source_date": "2026-09-13", "data": {
    "lmarena_text": {"date": "2026-09-13", "entries": [
        {"name": "big-5-max", "score": 1500}, {"name": "big-5-high", "score": 1450},
        {"name": "small-5", "score": 1300}, {"name": "someone-else", "score": 1200}]},
}}


class TestMatch(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("Anthropic: Claude Sonnet 5"), "claude-sonnet-5")
        self.assertEqual(normalize("openai/gpt-5.6-luna"), "gpt-5-6-luna")
        self.assertEqual(strip_variant(normalize("Claude Opus 5 (High)")), "claude-opus-5")
        self.assertEqual(strip_variant(normalize("claude-opus-4-5-20251101-high-32k")), "claude-opus-4-5")
        self.assertEqual(strip_variant(normalize("DeepSeek V4 Pro (High) (0813)")), "deepseek-v4-pro-0813")
        self.assertEqual(strip_variant(normalize("Qwen3-235B-A22B-Thinking (Jul 2025)")), "qwen3-235b-a22b")

    def test_index_prefers_standard_variant(self):
        idx = ModelIndex(MODELS["data"])
        self.assertNotIn("acme/big-5:batch", idx.models)
        self.assertNotIn("~acme/big-latest", idx.models)
        self.assertEqual(idx.resolve("big-5-max", "lmarena"), "acme/big-5")
        self.assertEqual(idx.resolve_usage_slug("acme/big-5-20260101"), "acme/big-5")
        self.assertIsNone(idx.resolve("someone-else", "lmarena"))


class TestScoring(unittest.TestCase):
    def test_percentiles(self):
        self.assertEqual(percentiles([1, 2, 3]), [0.0, 50.0, 100.0])
        self.assertEqual(percentiles([5]), [100.0])
        self.assertEqual(percentiles([1, 1]), [50.0, 50.0])

    def run_query(self, **q):
        return recommend(MODELS, USAGE, LMARENA, None, {"top": 10, "min_quality": 0, **q}, today="2026-09-17")

    def test_formula_is_reproducible(self):
        res = self.run_query(task="general")
        big = next(r for r in res["results"] if r["id"] == "acme/big-5")
        # AA : percentile 100 ; LMArena : meilleure entrée (max, 1500) = percentile 100
        self.assertEqual(big["n_sources"], 2)
        self.assertAlmostEqual(big["quality"], (100 + 100 + PRIOR) / 3, places=1)
        blended = 0.8 * 5 + 0.2 * 25
        self.assertAlmostEqual(big["blended_price_per_m"], blended)
        expected = big["quality"] * (1 + 0.25 * big["adoption"] / 100) / blended ** 0.5
        self.assertAlmostEqual(big["score"], expected, delta=0.02)

    def test_filters(self):
        ids = lambda res: [r["id"] for r in res["results"]]  # noqa: E731
        self.assertEqual(ids(self.run_query(min_context=1_000_000)), ["acme/big-5"])
        self.assertEqual(ids(self.run_query(open_weights=True)), ["open/lite-2"])
        self.assertEqual(ids(self.run_query(authors=["open"])), ["open/lite-2"])
        self.assertEqual(ids(self.run_query(sort="price"))[0], "open/lite-2")
        self.assertNotIn("open/lite-2:free", ids(self.run_query(include_free=True)))  # variante, jamais listée
        self.assertEqual(ids(self.run_query(input_modalities=["image"])), [])

    def test_unscored_popular_is_reported_not_ranked(self):
        res = self.run_query()
        self.assertNotIn("acme/unscored", [r["id"] for r in res["results"]])
        self.assertEqual(res["unscored_popular"][0]["id"], "acme/unscored")

    def test_value_default_quality_floor(self):
        res = recommend(MODELS, USAGE, LMARENA, None, {"sort": "value"}, today="2026-09-17")
        self.assertTrue(all(r["quality"] >= 60 for r in res["results"]))


PERF = [{"source_date": "2026-09-17", "measured_at": "2026-09-17T10:00:00+00:00", "data": {
    # big-5 : deux hébergeurs sains pondérés par requêtes + un hébergeur dégradé ignoré
    "acme/big-5": [
        {"provider": "A", "status": 0, "latency_ms": 1000, "throughput_tps": 40, "requests": 300},
        {"provider": "B", "status": 0, "latency_ms": 2000, "throughput_tps": 80, "requests": 100},
        {"provider": "C", "status": -5, "latency_ms": 30000, "throughput_tps": 5, "requests": 500},
    ],
    "acme/small-5": [{"provider": "A", "status": 0, "latency_ms": 300, "throughput_tps": 200, "requests": 50}],
    "open/lite-2": [{"provider": "A", "status": 0, "latency_ms": 5000, "throughput_tps": 20, "requests": 50}],
}}]


class TestSpeed(unittest.TestCase):
    def run_query(self, **q):
        return recommend(MODELS, USAGE, LMARENA, None, {"top": 10, "min_quality": 0, **q},
                         today="2026-09-17", perf_history=PERF)

    def test_perf_weighted_by_requests_and_degraded_ignored(self):
        from modelpicker.scoring import build_perf
        big = build_perf(PERF)["by_model"]["acme/big-5"]
        self.assertAlmostEqual(big["latency_ms"], (1000 * 300 + 2000 * 100) / 400)
        self.assertAlmostEqual(big["throughput_tps"], (40 * 300 + 80 * 100) / 400)
        self.assertEqual(big["fastest_provider"]["provider"], "B")

    def test_fast_sort_and_formula(self):
        res = self.run_query(sort="fast")
        rows = {r["id"]: r for r in res["results"]}
        small = rows["acme/small-5"]
        self.assertEqual(small["speed"]["responsiveness"], 100.0)  # le plus rapide sur les deux axes
        self.assertAlmostEqual(small["fast_score"], small["score"] * 1.5, delta=0.02)
        self.assertEqual(res["results"][0]["id"], "acme/small-5")
        self.assertIn("fast", res["formula"])

    def test_speed_filters_exclude_unmeasured(self):
        ids = lambda res: [r["id"] for r in res["results"]]  # noqa: E731
        self.assertEqual(set(ids(self.run_query(max_latency_ms=1500))), {"acme/big-5", "acme/small-5"})
        self.assertEqual(ids(self.run_query(min_throughput=100)), ["acme/small-5"])
        no_perf = recommend(MODELS, USAGE, LMARENA, None, {"sort": "fast", "min_quality": 0}, today="2026-09-17")
        self.assertEqual(no_perf["results"], [])


if __name__ == "__main__":
    unittest.main()
