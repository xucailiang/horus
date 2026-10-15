import json

from typer.testing import CliRunner

from horus.cli import app
from horus.core.service import Service
from tests.support import FIXTURES, reviewed_regression_plan


def test_cli_blocks_unapproved_run_with_exit_three(tmp_path):
    service = Service(tmp_path, read_roots=[FIXTURES])
    project, plan, _ = reviewed_regression_plan(service, "http://127.0.0.1:1")
    result = CliRunner().invoke(
        app, ["run", "--workspace", str(tmp_path), "--project-ref", project, "--plan-ref", plan]
    )
    assert result.exit_code == 3
    assert json.loads(result.stdout)["code"] == "review_required"


def test_cli_exports_and_imports_existing_review_without_creating_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_TEST_MODE", "1")
    source = tmp_path / "source"
    service = Service(source, read_roots=[FIXTURES])
    project, _, review = reviewed_regression_plan(service, "http://127.0.0.1:1")
    path = tmp_path / "reviewed.json"
    runner = CliRunner()
    exported = runner.invoke(
        app,
        [
            "plan",
            "export",
            "--workspace",
            str(source),
            "--project-ref",
            project,
            "--review-ref",
            review,
            "--out",
            str(path),
        ],
    )
    assert exported.exit_code == 0, exported.output
    imported = runner.invoke(
        app, ["plan", "import", "--workspace", str(tmp_path / "ci"), "--bundle", str(path)]
    )
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.stdout)["reviewRef"] == review
