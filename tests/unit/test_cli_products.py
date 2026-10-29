"""CLI smoke：finch products list / show / explore（无真实网络 / LLM）。"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.products.ids import product_id_for, solution_id_for
from finch.products.models import Product, Solution
from finch.products.repository import ProductRepository, SolutionRepository
from finch.settings import Paths, Settings
from finch.storage.workspace import Workspace


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


def _seed_product(ws: Workspace) -> str:
    pid = product_id_for("github.com/acme/widget")
    ProductRepository(ws).upsert(
        Product(id=pid, key="github.com/acme/widget", name="Widget", score_total=0.9)
    )
    return pid


def test_products_list_and_show_json(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    pid = _seed_product(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["products", "list", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert [p["id"] for p in payload] == [pid]

    r2 = CliRunner().invoke(app, ["products", "show", pid, "--json"])
    assert r2.exit_code == 0, r2.output
    assert json.loads(r2.output)["name"] == "Widget"


def test_products_explore_returns_stored_solution(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    pid = _seed_product(ws)
    expected_id = solution_id_for(pid, "", version="product-solution-v1")
    SolutionRepository(ws).upsert(
        Solution(id=expected_id, product_id=pid, who_problem="QA flaky tests")
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    # --skip-feedback：离线冒烟，不触发定向反馈搜索。
    r = CliRunner().invoke(app, ["products", "explore", pid, "--json", "--skip-feedback"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["id"] == expected_id


def test_products_show_missing_exits_1(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["products", "show", "prod_missing", "--json"])
    assert r.exit_code == 1
