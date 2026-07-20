from eval.score import aggregate, calibration_rows, is_complete, score_case


def result(case="c1", findings=(), cost=0.3, duration=60.0):
    return {
        "case": case,
        "findings": [{"confidence": c} for c in findings],
        "totals": {"cost_usd": cost, "duration_s": duration},
    }


def judgment(matches, validity, matchable=None):
    return {
        "ground_truth_matches": [
            {"ground_truth": f"g{i+1}", "matched_finding": m, "note": "",
             "matchable": True if matchable is None else matchable[i]}
            for i, m in enumerate(matches)
        ],
        "finding_validity": [
            {"finding": i, "valid": v, "note": ""} for i, v in enumerate(validity)
        ],
    }


def test_score_case_counts():
    row = score_case(
        result(findings=[0.9, 0.5, 0.6]),
        judgment(matches=[0, None, 2], validity=[True, False, "uncertain"]),
    )
    assert row["gt_matched"] == 2 and row["gt_total"] == 3
    assert row["valid"] == 1 and row["invalid"] == 1 and row["uncertain"] == 1


def test_aggregate_metrics():
    rows = [
        score_case(result(findings=[0.9, 0.5]), judgment([0, None], [True, True])),
        score_case(result(findings=[0.8]), judgment([None, None, 0], [False])),
    ]
    agg = aggregate(rows)
    assert agg["gt_total"] == 5 and agg["gt_matched"] == 2
    assert agg["recall"] == 2 / 5
    assert agg["precision"] == 2 / 3  # 2 valid of 3 judged findings
    assert abs(agg["mean_cost_usd"] - 0.3) < 1e-9


def test_recall_over_matchable_only():
    # 3 GT items, only 2 matchable (g2 fixed during review), 1 matched
    rows = [score_case(
        result(findings=[0.9]),
        judgment([0, None, None], [True], matchable=[True, False, True]),
    )]
    agg = aggregate(rows)
    assert rows[0]["gt_matchable"] == 2
    assert agg["recall"] == 1 / 2       # over matchable
    assert agg["recall_raw"] == 1 / 3   # over all GT


def test_missing_matchable_flag_defaults_to_matchable():
    j = judgment([0, None], [True])
    for m in j["ground_truth_matches"]:
        del m["matchable"]
    row = score_case(result(findings=[0.9]), j)
    assert row["gt_matchable"] == 2


def test_uncertain_counts_against_precision():
    rows = [score_case(result(findings=[0.9, 0.9]),
                       judgment([0], [True, "uncertain"]))]
    assert aggregate(rows)["precision"] == 0.5


def test_calibration_buckets():
    results = [result(findings=[0.45, 0.6, 0.6, 0.95])]
    judgments = [judgment([None], [True, True, False, True])]
    rows = calibration_rows(results, judgments)
    by_bucket = {r["bucket"]: r for r in rows}
    assert by_bucket["0.40-0.55"]["n"] == 1
    assert by_bucket["0.40-0.55"]["validity_rate"] == 1.0
    assert by_bucket["0.55-0.70"]["n"] == 2
    assert by_bucket["0.55-0.70"]["validity_rate"] == 0.5
    assert by_bucket["0.85-1.00"]["validity_rate"] == 1.0
    assert by_bucket["0.70-0.85"]["validity_rate"] is None


def test_is_complete():
    done = judgment([0, None], [True, False])
    done["ground_truth_matches"][1]["note"] = "no finding covers this"
    assert is_complete(done)
    assert not is_complete(judgment([0, None], [True, False]))  # null match, no note
    assert not is_complete(judgment([0], [None]))  # unjudged finding
