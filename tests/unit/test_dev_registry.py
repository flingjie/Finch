from finch.dev.registry import REGISTRY, DevContext
from finch.settings import Settings


def test_registry_has_unique_names_and_valid_kinds():
    assert len(REGISTRY) >= 6
    for name, spec in REGISTRY.items():
        assert spec.name == name
        assert spec.kind in ("graph_node", "function")


def test_graph_node_build_returns_node():
    ctx = DevContext(settings=Settings())
    for name, expected_reads in {
        "recall": {"candidates", "evidence_cards"},
    }.items():
        spec = REGISTRY[name]
        assert spec.kind == "graph_node"
        node = spec.build(ctx)
        assert node.name == name
        assert set(node.reads) == expected_reads


def test_function_has_no_build():
    for name in ("select_groups", "rank_pending", "select_primary_job",
                 "select_planning_evidence", "scan_cards"):
        spec = REGISTRY[name]
        assert spec.kind == "function"
        assert spec.build is None
        assert spec.test_targets
