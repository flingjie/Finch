from finch.dev.registry import REGISTRY


def test_registry_has_unique_names_and_valid_kinds():
    assert len(REGISTRY) >= 4
    for name, spec in REGISTRY.items():
        assert spec.name == name
        assert spec.kind == "function"


def test_function_specs_have_test_targets():
    for name in ("select_groups", "rank_pending",
                 "select_planning_evidence", "scan_cards"):
        spec = REGISTRY[name]
        assert spec.kind == "function"
        assert spec.test_targets
