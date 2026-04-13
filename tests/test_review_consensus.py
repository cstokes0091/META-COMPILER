from meta_compiler.stages.review_stage import compute_consensus


def test_consensus_unanimous_proceed():
    verdicts = {
        "optimistic": {"verdict": "PROCEED"},
        "pessimistic": {"verdict": "PROCEED"},
        "pragmatic": {"verdict": "PROCEED"},
    }
    consensus = compute_consensus(verdicts, iteration_count=0)
    assert consensus["decision"] == "PROCEED"
    assert consensus["reason"] == "unanimous_proceed"


def test_consensus_iteration_cap_forces_proceed():
    verdicts = {
        "optimistic": {"verdict": "ITERATE"},
        "pessimistic": {"verdict": "ITERATE"},
        "pragmatic": {"verdict": "ITERATE"},
    }
    consensus = compute_consensus(verdicts, iteration_count=3)
    assert consensus["decision"] == "PROCEED"
    assert consensus["forced"] is True
