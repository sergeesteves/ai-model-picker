"""Tests de l'app web (sans réseau ni LLM réel) : charte, liste blanche, question libre, plafonds, cache."""
import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("COLLECT_ON_STARTUP", "false")

try:
    from fastapi.testclient import TestClient
except ImportError:  # dépendances web absentes : tests ignorés
    TestClient = None

from modelpicker.query import sanitize_query
from tests.test_core import LMARENA, MODELS, USAGE

CSS = (Path(__file__).resolve().parents[1] / "web" / "static" / "style.css").read_text(encoding="utf-8")

# Source de vérité : brand/visual-identity.md (repo sergeesteves/creapulse-knowledge-base)
CHARTE = {
    "--cp-accent": "#e61781", "--cp-accent-fill": "#e2177f", "--cp-accent-hover": "#be136b",
    "--cp-primary": "#029ae5", "--cp-primary-fill": "#027dba", "--cp-primary-hover": "#01699c",
    "--cp-ink": "#111111", "--cp-ink-muted": "#595959",
    "--cp-rule": "rgba(0, 0, 0, 0.12)", "--cp-surface-subtle": "rgba(0, 0, 0, 0.03)",
}


class TestBrand(unittest.TestCase):
    def token(self, name):
        m = re.search(rf"{re.escape(name)}:\s*([^;]+);", CSS)
        self.assertIsNotNone(m, name)
        return m.group(1).strip()

    def test_tokens_match_visual_identity(self):
        for name, value in CHARTE.items():
            self.assertEqual(self.token(name), value, name)

    def test_quiet_hover_turns_label_white(self):
        rule = re.search(r"button\.quiet:hover[^{]*\{([^}]*)\}", CSS).group(1)
        self.assertIn("background: var(--cp-primary-fill)", rule)
        self.assertIn("color: #fff", rule)

    def test_every_task_source_has_a_browser_label(self):
        import json
        root = Path(__file__).resolve().parents[1]
        js = (root / "web" / "static" / "app.js").read_text(encoding="utf-8")
        tasks = json.loads((root / "data" / "tasks.json").read_text(encoding="utf-8"))
        for name, task in tasks.items():
            if name.startswith("_"):
                continue
            for src in task["sources"]:
                self.assertRegex(js, rf"{src}:", f"abréviation manquante dans app.js : {src}")

    def test_single_cta_in_template(self):
        html = (Path(__file__).resolve().parents[1] / "web" / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r'class="cta"', html)), 1)


class TestSanitize(unittest.TestCase):
    def test_whitelist(self):
        q = sanitize_query({"task": "hack", "sort": "drop table", "authors": ["openai", "evil"], "min_context": "abc",
                            "max_price": -3, "min_quality": 250, "top": 999, "input_modalities": ["image", "x"],
                            "open_weights": "oui"}, {"openai"})
        self.assertEqual((q["task"], q["sort"], q["authors"]), ("general", "value", ["openai"]))
        self.assertIsNone(q["min_context"]); self.assertIsNone(q["max_price"]); self.assertIsNone(q["min_quality"])
        self.assertEqual(q["top"], 5); self.assertEqual(q["input_modalities"], ["image"]); self.assertTrue(q["open_weights"])


@unittest.skipIf(TestClient is None, "fastapi non installé")
class TestApi(unittest.TestCase):
    def setUp(self):
        from web import main
        from web.data import store
        from web.limits import DailyBudget, QuestionCache
        self.main = main
        store.models, store.history, store.lmarena, store.epoch = MODELS, USAGE, LMARENA, None
        store.authors = {"acme", "open"}
        main.budget = DailyBudget(global_cap=2, ip_cap=5)
        main.ask_cache = QuestionCache(10)
        main.app.state.http = None
        self.calls = 0

        async def fake_llm(question, authors, client):
            self.calls += 1
            if "météo" in question:
                return {"off_topic": True}, {}
            return {"task": "code", "sort": "price", "authors": ["acme", "inventé"], "min_quality": 0}, {}

        main.question_to_query = fake_llm
        self.client = TestClient(main.app)

    def test_recommend_form(self):
        r = self.client.get("/api/recommend", params={"task": "general", "author": "open", "sort": "quality"})
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertEqual([m["id"] for m in data["result"]["results"]], ["open/lite-2"])
        self.assertIn("Meilleure qualité", data["summary"])

    def test_ask_uses_whitelist_and_cache(self):
        r = self.client.post("/api/ask", json={"question": "Le moins cher chez Acme pour coder ?"})
        data = r.json()
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["query"]["authors"], ["acme"])  # « inventé » écarté
        r2 = self.client.post("/api/ask", json={"question": "le moins cher chez acme pour coder"})
        self.assertTrue(r2.json()["cached_question"])
        self.assertEqual(self.calls, 1)

    def test_global_cap_then_form_still_works(self):
        for q in ("question numéro un", "question numéro deux"):
            self.assertTrue(self.client.post("/api/ask", json={"question": q}).json()["ok"])
        r = self.client.post("/api/ask", json={"question": "question numéro trois"})
        self.assertEqual((r.status_code, r.json()["code"]), (429, "global_cap"))
        self.assertTrue(self.client.get("/api/recommend").json()["ok"])

    def test_off_topic_and_input_checks(self):
        r = self.client.post("/api/ask", json={"question": "quelle météo demain à Lyon"})
        self.assertEqual(r.json()["code"], "off_topic")
        self.assertEqual(self.client.post("/api/ask", json={"question": "x"}).json()["code"], "input")
        self.assertEqual(self.client.post("/api/ask", json={"question": "a" * 400}).json()["code"], "input")

    def test_page_and_health(self):
        self.assertIn("Quel modèle IA choisir", self.client.get("/").text)
        self.assertTrue(self.client.get("/health").json()["data_ready"])


if __name__ == "__main__":
    unittest.main()
