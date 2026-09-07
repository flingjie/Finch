"""finch dev CLI（typer）：列出/测试功能点，无生产副作用。"""

import typer

from .registry import REGISTRY
from .runner import list_features, run_test

dev_app = typer.Typer(help="开发 harness：列出/测试功能点（不触发生产副作用）。")


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
