"""Schema validation for every ground-truth case in eval/dataset/."""

import re
from pathlib import Path

import pytest
import yaml

DATASET_DIR = Path(__file__).parent.parent / "eval" / "dataset"
CASES = sorted(DATASET_DIR.glob("*.yaml"))

REQUIRED_FIELDS = {
    "url", "repo", "pr", "title", "pre_merge_sha",
    "changed_lines", "selected_because", "ground_truth",
}
CATEGORIES = {"correctness", "security", "style", "test", "other"}


def test_dataset_exists_once_curated():
    # Placeholder guard: once curation lands, this directory must not be empty.
    # (Skipped rather than failed while the dataset is being assembled.)
    if not CASES:
        pytest.skip("dataset not yet curated")


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_case_schema(path):
    case = yaml.safe_load(path.read_text())
    missing = REQUIRED_FIELDS - case.keys()
    assert not missing, f"missing fields: {missing}"

    owner_repo = case["repo"]
    assert re.fullmatch(r"[\w.-]+/[\w.-]+", owner_repo)
    assert case["url"] == f"https://github.com/{owner_repo}/pull/{case['pr']}"
    assert path.stem == f"{owner_repo.split('/')[1]}_{case['pr']}"
    assert re.fullmatch(r"[0-9a-f]{40}", case["pre_merge_sha"])
    assert 50 <= case["changed_lines"] <= 800, "spec: 50-800 changed lines"

    issues = case["ground_truth"]
    assert 1 <= len(issues) <= 5, "spec: 1-5 substantive issues per PR"
    ids = [g["id"] for g in issues]
    assert ids == [f"g{i + 1}" for i in range(len(issues))], "ids must be g1..gN"
    for g in issues:
        assert g["category"] in CATEGORIES
        assert g["issue"].strip()
        assert g["evidence"].startswith("https://github.com/")
