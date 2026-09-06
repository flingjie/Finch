"""finch dev CLI（typer）：列出/测试/运行功能点，无生产副作用。"""

import json
from pathlib import Path

import typer

from finch.settings import load_settings

from .registry import REGISTRY
from .runner import list_features, run_node, run_test

dev_app = typer.Typer(help="开发 harness：列出/测试/运行功能点（不触发生产副作用）。")


@dev_app.command("list-features")
def list_features_cmd() -> None:
    for spec in list_features():
        typer.echo(f"{spec.name}\t{spec.kind}\t{spec.description}")


@dev_app.command("test")
def test_cmd(
    feature: str = typer.Argument(..., help="FeatureSpec name (see list-features)"),
) -> None:
    spec = REGISTRY.get(feature)
    if spec is None:
        typer.echo(f"unknown feature: {feature}; run `finch dev list-features`")
        raise typer.Exit(code=1)
    raise typer.Exit(code=run_test(spec))


@dev_app.command("run-node")
def run_node_cmd(
    node: str = typer.Argument(..., help="graph_node name (see list-features)"),
    input_path: Path = typer.Option(..., "--input", help="GraphContext envelope JSON"),  # noqa: B008
) -> None:
    settings = load_settings()
    try:
        result = run_node(node, input_path, settings)
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    if result.status != "succeeded":
        raise typer.Exit(code=1)
