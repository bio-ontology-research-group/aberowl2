#!/usr/bin/env python3
"""Reproduce the paper's evaluation offline, using Python's standard library.

From the repository root:
    python3 experiments/reproduce.py --out-dir /tmp/aberowl-reproduction

The output directory must be empty. Inputs are explicit; partial runs and old
derived scores are never discovered by a wildcard. No manuscript checkout,
credentials, ontology server or model access is needed.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
GROUNDING = Path("experiments/iri_hallucination")
REASONING = Path("experiments/dl_reasoning")
MODELS = (
    "google/gemini-3.5-flash", "deepseek/deepseek-v3.2",
    "meta-llama/llama-4-scout", "qwen/qwen3.6-35b-a3b", "openai/gpt-oss-20b",
)
DL_FILES = tuple(REASONING / ("runs_" + m.split("/")[-1] + ".jsonl") for m in MODELS)
CONDITIONS = ("none", "lookup", "dlquery", "dlquery_hint")
CLASS_FILES = tuple(REASONING / ("classes_" + ont + ".txt") for ont in ("go", "cl", "so"))

# Transcribed from manuscript Tables 4 and 5. These are checks on rescored
# results, never inputs to answer selection or scoring.
EXPECTED_GROUNDING = {
    "google/gemini-3.5-flash": [36.1, 95.9, 2.5, 0.0, 52.5, 95.0],
    "deepseek/deepseek-v3.2": [27.0, 96.7, 1.7, 0.0, 27.5, 97.5],
    "meta-llama/llama-4-scout": [6.6, 92.6, 12.3, 3.3, 15.0, 82.5],
    "qwen/qwen3.6-35b-a3b": [9.0, 95.9, 6.6, 0.0, 85.0, 90.0],
    "openai/gpt-oss-20b": [6.6, 96.7, 21.5, 0.0, 92.5, 85.0],
}
EXPECTED_DL_COUNTS = {
    "google/gemini-3.5-flash": [0, 0, 4, 2, 54, 58, 54, 56],
    "deepseek/deepseek-v3.2": [0, 0, 7, 7, 57, 52, 57, 46],
    "meta-llama/llama-4-scout": [0, 0, 0, 2, 59, 60, 50, 39],
    "qwen/qwen3.6-35b-a3b": [0, 0, 6, 7, 53, 59, 56, 52],
    "openai/gpt-oss-20b": [0, 0, 0, 5, 56, 58, 59, 21],
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read_rows(path):
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def item_key(row):
    return row["term"], row.get("ontology")


def unique_index(rows, key, label):
    result = {}
    for row in rows:
        k = key(row)
        require(k not in result, f"Duplicate {label}: {k}")
        result[k] = row
    return result


def validate_reasoning(rows, gold):
    index = unique_index(gold, item_key, "reasoning gold item")
    require(len(index) == 120, "Expected 120 reasoning gold items")
    balance = Counter((g["ontology"], g["task"]) for g in gold)
    require(balance == Counter({(o, t): 20 for o in ("go", "cl", "so") for t in ("T1", "T2")}),
            "Reasoning gold ontology/task balance differs from the paper")
    seen = unique_index(rows, lambda r: (r["model"], r["condition"], *item_key(r)), "reasoning run")
    expected = {(m, c, *key) for m in MODELS for c in CONDITIONS for key in index}
    require(set(seen) == expected, "Reasoning runs have missing or unexpected model/condition/item keys")
    require(not any(r.get("error") for r in rows), "Reasoning inputs contain error rows")
    return index


def validate_grounding(raw, original_gold, gold):
    index = unique_index(gold, item_key, "grounding gold item")
    require(len(index) == 162 and sum(g["gold_iri"] is not None for g in gold) == 122,
            "Expected 122 positive and 40 negative grounding items")
    require(len(original_gold) == 173 and len(raw) == 3450,
            "Original grounding gold/run count differs from the published inputs")
    original_keys = {(r["term"], r.get("ontology"), r["difficulty"]) for r in original_gold}
    # Repeated prompts are intentional here and retained for the repeat analysis.
    for row in raw:
        require((row["term"], row.get("ontology"), row["difficulty"]) in original_keys,
                "Grounding response not present in original gold")
        require(row.get("gold_iri") == index[item_key(row)]["gold_iri"], "Grounding gold IRI mismatch")
        require(row["model"] in MODELS and row["condition"] in ("none", "find_iri")
                and row["regime"] in ("forced", "abstain"), "Unexpected grounding condition")
        require(not row.get("error"), "Grounding inputs contain error rows")


def grounding_table(rows):
    table = {}
    for model in MODELS:
        positives = {c: [r for r in rows if r["model"] == model and r["regime"] == "forced"
                         and r["condition"] == c and r["gold_iri"] is not None]
                     for c in ("none", "find_iri")}
        negatives = {c: [r for r in rows if r["model"] == model and r["regime"] == "abstain"
                         and r["condition"] == c and r["gold_iri"] is None]
                     for c in ("none", "find_iri")}
        acc = [round(100 * sum(r["_lab"] == "correct" for r in b) / len(b), 1)
               for b in positives.values()]
        hall = [round(100 * sum(r["_lab"] == "hallucinated" for r in b)
                      / sum(r["_lab"] != "unknown" for r in b), 1) for b in positives.values()]
        decline = [round(100 * sum(r["_got"] is None for r in b) / len(b), 1) for b in negatives.values()]
        table[model] = acc + hall + decline
    return table


def reasoning_table(rows):
    table = {}
    for model in MODELS:
        cells = [[r for r in rows if r["model"] == model and r["condition"] == c and r["task"] == t]
                 for c in CONDITIONS for t in ("T1", "T2")]
        require(all(len(cell) == 60 for cell in cells), "Reasoning cells must each contain 60 items")
        table[model] = [sum(r["exact_set"] for r in cell) for cell in cells]
    return table


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_script(script, args, log):
    with log.open("w") as fh:
        completed = subprocess.run([sys.executable, "-I", str(ROOT / script), *map(str, args)],
                                   cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
    require(completed.returncode == 0, f"Scorer failed: see {log}")


def reproduce(out):
    require(not out.exists() or (out.is_dir() and not any(out.iterdir())),
            "Output directory must be empty; choose a new --out-dir")
    inputs = [GROUNDING / f for f in ("gold.jsonl", "gold_dedup.jsonl", "runs_full.jsonl", "iri_exists_resolved.json", "replicates.json")]
    inputs += [REASONING / "gold_all.jsonl", *DL_FILES, *CLASS_FILES,
               GROUNDING / "direct_lookup/responses.jsonl"]
    for rel in inputs:
        require((ROOT / rel).is_file(), f"Missing input: {rel}")
    hashes = {str(rel): hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() for rel in inputs}
    g_raw = read_rows(ROOT / GROUNDING / "runs_full.jsonl")
    g_gold = read_rows(ROOT / GROUNDING / "gold_dedup.jsonl")
    validate_grounding(g_raw, read_rows(ROOT / GROUNDING / "gold.jsonl"), g_gold)
    dl_gold = read_rows(ROOT / REASONING / "gold_all.jsonl")
    dl_raw = []
    for model, rel in zip(MODELS, DL_FILES):
        batch = read_rows(ROOT / rel)
        require(len(batch) == 480 and {r["model"] for r in batch} == {model},
                f"Expected 480 runs of {model} in {rel}")
        dl_raw.extend(batch)
    validate_reasoning(dl_raw, dl_gold)

    out.mkdir(parents=True, exist_ok=True)
    ground_out, dl_out, baseline_out = (out / name for name in ("grounding", "reasoning", "direct_lookup"))
    for folder in (ground_out, dl_out, baseline_out):
        folder.mkdir()
    common = ["--runs", ROOT / GROUNDING / "runs_full.jsonl", "--gold", ROOT / GROUNDING / "gold_dedup.jsonl",
              "--map", ROOT / GROUNDING / "iri_exists_resolved.json"]
    for suffix, extra in (("aggregate", ["--out", ground_out / "scored.jsonl"]),
                          ("by_stratum", ["--by-stratum"]), ("by_difficulty", ["--by-difficulty"])):
        run_script(GROUNDING / "score.py", common + extra, ground_out / (suffix + ".txt"))
    run_script(GROUNDING / "dedup_gold.py",
               ["--gold", ROOT / GROUNDING / "gold.jsonl", "--runs", ROOT / GROUNDING / "runs_full.jsonl",
                "--out", ground_out / "gold_dedup.jsonl", "--replicates", ground_out / "replicates.json"],
               ground_out / "repeats.txt")
    require(read_rows(ground_out / "gold_dedup.jsonl") == g_gold, "Regenerated grounding gold differs")
    run_script(REASONING / "score_dl.py", ["--gold", ROOT / REASONING / "gold_all.jsonl", "--runs",
               *[ROOT / p for p in DL_FILES], "--classes", *[ROOT / p for p in CLASS_FILES],
               "--out", dl_out / "scored.jsonl"], dl_out / "metrics.txt")
    run_script(GROUNDING / "direct_lookup/score.py", ["--out-dir", baseline_out], baseline_out / "metrics.txt")

    g_scored = read_rows(ground_out / "scored.jsonl")
    dl_scored = read_rows(dl_out / "scored.jsonl")
    require(len(g_scored) == 3231, "Expected 3231 scored grounding rows")
    existence = json.loads((ROOT / GROUNDING / "iri_exists_resolved.json").read_text())
    existence = existence.get("map", existence)
    require(all(r["_got"] in existence for r in g_scored if r["_lab"] == "unknown"),
            "A produced identifier is missing from the existence map")
    unique_index(g_scored, lambda r: (r["model"], r["regime"], r["condition"], *item_key(r)), "scored grounding run")
    require(grounding_table(g_scored) == EXPECTED_GROUNDING, "Grounding table differs from manuscript")
    require(reasoning_table(dl_scored) == EXPECTED_DL_COUNTS, "Reasoning table differs from manuscript")
    repeats = json.loads((ground_out / "replicates.json").read_text())
    require(repeats["identical_prompt_pairs"] == 239, "Repeat-pair count differs from manuscript")
    require(repeats == json.loads((ROOT / GROUNDING / "replicates.json").read_text()), "Repeat diagnostics differ")
    baseline = json.loads((baseline_out / "summary.json").read_text())
    require((baseline["positive_correct"], baseline["positive_ambiguous"], baseline["negative_no_exact_match"])
            == (117, 5, 40), "Direct grounding baseline differs from manuscript")
    diagnostics = load_module(ROOT / "experiments/reproduction_diagnostics.py", "reproduction_diagnostics")
    reasoning = diagnostics.reasoning_diagnostics(dl_raw, dl_gold, dl_scored)
    paired = diagnostics.paired_contrasts(g_scored, dl_scored)
    require(reasoning["unhinted"] == {
        "runs": 600, "adopted": 599, "first_formulation_strict": 493,
        "last_formulation_strict": 477, "best_formulation_strict": 538,
        "best_formulation_normalized": 585, "relay_strict": 537, "relay_normalized": 578,
        "failures": {"no_adoption": 1, "formulation": 12, "relay": 13, "anchor_inclusion": 8},
    }, "Reasoning diagnostics differ from manuscript")
    require(reasoning["recorded_reasoning_calls"] == {
        "calls": 2017, "at_log_cap": 11, "log_cap_characters": 6000,
    }, "Recorded tool-result counts differ from manuscript")
    expected_pairs = {
        "grounding_negative_abstain": [(17, 0), (29, 1), (28, 1), (3, 1), (2, 5)],
        "reasoning_T2_hint": [(0, 2), (7, 13), (0, 21), (0, 7), (1, 38)],
    }
    for name, pairs in expected_pairs.items():
        for model, (gains, losses) in zip(MODELS, pairs):
            result = paired[name][model]
            require((result["gains"], result["losses"], result["pairs"])
                    == (gains, losses, 40 if name == "grounding_negative_abstain" else 60),
                    f"Paired contrast differs from manuscript: {name}, {model}")
    (dl_out / "diagnostics.json").write_text(json.dumps(reasoning, indent=2) + "\n")
    (out / "paired_contrasts.json").write_text(json.dumps(paired, indent=2) + "\n")
    report = {
        "status": "PASS", "input_sha256": hashes,
        "grounding_scored_runs": len(g_scored), "reasoning_scored_runs": len(dl_scored),
        "grounding_table_columns": ["positive_accuracy_none", "positive_accuracy_find_iri",
                                    "positive_hallucination_none", "positive_hallucination_find_iri",
                                    "negative_declines_none", "negative_declines_find_iri"],
        "grounding_table_percent": grounding_table(g_scored),
        "reasoning_table_columns": [c + "/" + t for c in CONDITIONS for t in ("T1", "T2")],
        "reasoning_correct_per_60": reasoning_table(dl_scored),
        "repeat_pairs": repeats["identical_prompt_pairs"], "direct_lookup": baseline,
        "reasoning_diagnostics": reasoning["unhinted"],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "README.txt").write_text(
        "Offline reproduction: PASS\n"
        "grounding/: aggregate, strata and difficulty metrics; per-response classifications; repeats.\n"
        "reasoning/: per-run scores, per-cell metrics, first/last/best-call diagnostics.\n"
        "direct_lookup/: single-correct-exact-match baseline and per-item outcomes.\n"
        "paired_contrasts.json: exploratory unadjusted two-sided exact McNemar tests.\n"
        "report.json: checked manuscript table values and input checksums.\n"
        "This rescoring does not rerun models or verify historical service/ontology versions.\n")
    print(f"PASS: 3231 grounding runs; 2400 reasoning runs; 239 repeat pairs; baseline 117/122 and 40/40.\nOutputs: {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "results/paper-reproduction")
    args = parser.parse_args()
    try:
        reproduce(args.out_dir.resolve())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
