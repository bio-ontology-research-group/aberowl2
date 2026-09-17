#!/usr/bin/env python3
"""Score saved scoped resolver responses offline (standard library only).

Positive success requires one distinct exact IRI equal to gold. Negative success
means no exact match, not agent abstention. Gold is used only for scoring.
"""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent


def read_rows(path):
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc
    rows = []
    seen = set()
    for line_number, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number}: expected an object")
        for field in ("term", "ontology"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"{path}:{line_number}: missing or invalid {field}")
        key = (row["term"], row["ontology"])
        if key in seen:
            raise ValueError(f"{path}:{line_number}: duplicate term/ontology {key!r}")
        seen.add(key)
        rows.append(row)
    if not rows:
        raise ValueError(f"{path}: empty input")
    return rows


def exact_iris(response):
    key = (response["term"], response["ontology"])
    if response.get("ok") is not True or response.get("status") != 200:
        raise ValueError(f"{key!r}: unsuccessful HTTP response")
    fetched_at = response.get("fetched_at")
    try:
        if not isinstance(fetched_at, str) or datetime.fromisoformat(fetched_at).tzinfo is None:
            raise ValueError("timestamp must include timezone")
    except ValueError as exc:
        raise ValueError(f"{key!r}: invalid fetched_at timestamp") from exc
    url = response.get("url")
    if not isinstance(url, str):
        raise ValueError(f"{key!r}: missing request URL")
    params = parse_qs(urlparse(url).query)
    if params.get("query") != [response["term"]] or params.get("ontologies") != [response["ontology"]]:
        raise ValueError(f"{key!r}: request URL does not match term/ontology")
    try:
        sizes = params.get("size", [])
        if len(sizes) != 1:
            raise ValueError("expected one size")
        size = int(sizes[0])
        if size <= 0:
            raise ValueError("size must be positive")
    except ValueError as exc:
        raise ValueError(f"{key!r}: missing or invalid request size") from exc
    data = response.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("result"), list):
        raise ValueError(f"{key!r}: missing or malformed result list")
    records = data["result"]
    if len(records) >= size:
        raise ValueError(f"{key!r}: response may be capped at request size {size}")
    result = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f"{key!r}: malformed resolver record")
        candidates = []
        for field in ("class", "owlClass"):
            value = record.get(field)
            if value is not None:
                if not isinstance(value, str) or not value.strip().strip("<>"):
                    raise ValueError(f"{key!r}: invalid record {field}")
                candidates.append(value.strip().strip("<>"))
        if not candidates or len(set(candidates)) != 1:
            raise ValueError(f"{key!r}: missing or inconsistent record IRI")
        iri = candidates[0]
        if iri not in result:
            result.append(iri)
    return result


def score(gold_path, input_path):
    gold = read_rows(gold_path)
    responses = read_rows(input_path)
    key = lambda row: (row["term"], row["ontology"])
    by_key = {key(row): row for row in responses}
    gold_keys = {key(row) for row in gold}
    if gold_keys != set(by_key):
        raise ValueError(f"Response coverage differs from gold: {len(gold_keys - set(by_key))} missing, "
                         f"{len(set(by_key) - gold_keys)} unexpected")
    scored = []
    for item in gold:
        if "gold_iri" not in item or (item["gold_iri"] is not None and
                (not isinstance(item["gold_iri"], str) or not item["gold_iri"].strip())):
            raise ValueError(f"{key(item)!r}: gold_iri must be a nonempty string or null")
        response = by_key[key(item)]
        matches = exact_iris(response)
        if item["gold_iri"] is None:
            outcome = "negative_no_exact_match" if not matches else "negative_with_exact_match"
        elif not matches:
            outcome = "positive_no_exact_match"
        elif len(matches) > 1:
            outcome = "positive_ambiguous"
        elif matches[0] == item["gold_iri"]:
            outcome = "positive_correct"
        else:
            outcome = "positive_wrong_exact_match"
        scored.append({**item, "fetched_at": response["fetched_at"], "exact_iris": matches,
                       "outcome": outcome,
                       "success": outcome in ("positive_correct", "negative_no_exact_match")})
    positives = [row for row in scored if row["gold_iri"] is not None]
    negatives = [row for row in scored if row["gold_iri"] is None]
    counts = Counter(row["outcome"] for row in scored)
    summary = {
        "measure": "single_correct_exact_match",
        "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (gold_path, input_path)},
        "n_positive": len(positives), "n_negative": len(negatives),
        "response_dates": sorted({row["fetched_at"][:10] for row in scored}),
        "ambiguous_positives": [{"term": row["term"], "ontology": row["ontology"]}
                                 for row in positives if row["outcome"] == "positive_ambiguous"],
        "positive_gold_in_exact_matches": sum(row["gold_iri"] in row["exact_iris"] for row in positives),
        **{name: counts[name] for name in ("positive_correct", "positive_ambiguous",
           "positive_no_exact_match", "positive_wrong_exact_match", "negative_no_exact_match",
           "negative_with_exact_match")},
        "positive_accuracy_percent": 100 * counts["positive_correct"] / len(positives) if positives else None,
        "negative_no_exact_match_percent": 100 * counts["negative_no_exact_match"] / len(negatives) if negatives else None,
    }
    return scored, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=HERE.parent / "gold_dedup.jsonl")
    parser.add_argument("--responses", type=Path, default=HERE / "responses.jsonl")
    parser.add_argument("--out-dir", type=Path, default=HERE)
    args = parser.parse_args()
    try:
        scored, summary = score(args.gold, args.responses)
    except ValueError as exc:
        parser.error(str(exc))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "scored.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in scored))
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
