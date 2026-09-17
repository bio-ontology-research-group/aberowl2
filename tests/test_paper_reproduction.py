"""Release-scoring checks; run directly with Python, without pytest dependencies."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load("reproduce_paper", "experiments/reproduce.py")
baseline = load("direct_lookup_score", "experiments/iri_hallucination/direct_lookup/score.py")
diagnostics = load("paper_diagnostics", "experiments/reproduction_diagnostics.py")


class ReproductionValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gold = runner.read_rows(ROOT / runner.REASONING / "gold_all.jsonl")
        cls.rows = [row for rel in runner.DL_FILES for row in runner.read_rows(ROOT / rel)]

    def test_complete_reasoning_inputs(self):
        self.assertEqual(len(runner.validate_reasoning(self.rows, self.gold)), 120)

    def test_partial_or_duplicate_reasoning_run_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate reasoning run"):
            runner.validate_reasoning(self.rows + [self.rows[0]], self.gold)
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            runner.validate_reasoning(self.rows[1:], self.gold)

    def test_missing_file_fails(self):
        with self.assertRaises(FileNotFoundError):
            runner.read_rows(ROOT / "missing-release-input.jsonl")

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            sentinel = Path(d) / "report.json"
            sentinel.write_text("previous result")
            with self.assertRaisesRegex(ValueError, "must be empty"):
                runner.reproduce(Path(d))
            self.assertEqual(sentinel.read_text(), "previous result")

    def test_best_call_is_not_first_or_last(self):
        # A middle call is correct, while first and last are wrong. Header IRIs
        # are deliberately distracting and must not enter the returned set.
        good, bad = "https://example.org/good", "https://example.org/bad"
        result = lambda iri: f"Found 1 result for <{bad}>\n label [test] - {iri}"
        row = {"model": "test", "condition": "dlquery", "term": "query", "ontology": "test",
               "answer": good, "tool_calls": [
                   {"tool": "run_dl_query", "result": result(i)} for i in (bad, good, bad)]}
        gold = {"term": "query", "ontology": "test", "task": "T1", "gold_iris": [good]}
        scored = {**row, "formulation_ok": True, "formulation_ok_strict": True,
                  "relay_ok": True, "relay_ok_strict": True}
        totals = diagnostics.reasoning_diagnostics([row], [gold], [scored])["unhinted"]
        self.assertEqual((totals["first_formulation_strict"], totals["last_formulation_strict"],
                          totals["best_formulation_strict"]), (0, 0, 1))

    def test_two_sided_paired_test_and_missing_pair(self):
        rows = []
        for i in range(7):
            for condition in ("none", "find_iri"):
                success = (i < 2) == (condition == "find_iri")
                rows.append({"condition": condition, "term": str(i), "ontology": "test", "success": success})
        result = diagnostics._paired(rows, "none", "find_iri", lambda r: r["success"])
        self.assertEqual((result["gains"], result["losses"], result["p_unadjusted"]), (2, 5, 0.453125))
        with self.assertRaisesRegex(ValueError, "Missing paired"):
            diagnostics._paired(rows[:-1], "none", "find_iri", lambda r: r["success"])


class BaselineValidationTests(unittest.TestCase):
    def setUp(self):
        self.response = {
            "term": "term", "ontology": "test", "ok": True, "status": 200,
            "url": "https://example.org/api/resolve?query=term&ontologies=test&size=25",
            "fetched_at": "2026-08-30T00:00:00+00:00", "data": {"result": []},
        }

    def score(self, gold, responses):
        with tempfile.TemporaryDirectory() as d:
            gp, rp = Path(d) / "gold.jsonl", Path(d) / "responses.jsonl"
            gp.write_text("".join(json.dumps(r) + "\n" for r in gold))
            rp.write_text("".join(json.dumps(r) + "\n" for r in responses))
            return baseline.score(gp, rp)

    def test_ambiguous_gold_hit_is_unsuccessful(self):
        gold = {"term": "term", "ontology": "test", "gold_iri": "https://example.org/gold"}
        self.response["data"]["result"] = [{"class": gold["gold_iri"]}, {"class": "https://example.org/other"}]
        rows, summary = self.score([gold], [self.response])
        self.assertFalse(rows[0]["success"])
        self.assertEqual((summary["positive_correct"], summary["positive_ambiguous"]), (0, 1))

    def test_failed_request_is_not_negative_detection(self):
        gold = {"term": "term", "ontology": "test", "gold_iri": None}
        self.response["ok"] = False
        with self.assertRaisesRegex(ValueError, "unsuccessful HTTP"):
            self.score([gold], [self.response])

    def test_duplicate_missing_and_capped_responses_fail(self):
        gold = {"term": "term", "ontology": "test", "gold_iri": None}
        with self.assertRaisesRegex(ValueError, "duplicate term"):
            self.score([gold], [self.response, copy.deepcopy(self.response)])
        other = {"term": "other", "ontology": "test", "gold_iri": None}
        with self.assertRaisesRegex(ValueError, "coverage differs"):
            self.score([gold, other], [self.response])
        self.response["data"]["result"] = [{"class": "https://example.org/x"}] * 25
        with self.assertRaisesRegex(ValueError, "may be capped"):
            self.score([gold], [self.response])


if __name__ == "__main__":
    unittest.main()
