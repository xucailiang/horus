import copy

import pytest

from horus.core.bundles import export_review, import_review
from horus.core.service import Service
from horus.core.store import HorusError
from tests.support import FIXTURES, reviewed_regression_plan


def test_review_bundle_replays_in_another_directory_with_the_same_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_TEST_MODE", "1")
    source = Service(tmp_path / "source", read_roots=[FIXTURES])
    project, plan, review = reviewed_regression_plan(source, "http://127.0.0.1:34567")
    package = export_review(source, project, review)
    destination = Service(tmp_path / "ci")
    imported = import_review(destination, package)
    assert imported["projectRef"] == project
    approval = destination.review_gate(project, plan, review, "test")
    config = destination.project(project).get("configs", approval["configRef"])
    assert config["environments"]["test"]["baseUrl"] == "http://127.0.0.1:34567"
    assert "HORUS_PASSWORD" in config["environments"]["test"]["secretBindings"].values()


def test_changed_bundle_is_rejected_before_creating_a_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_TEST_MODE", "1")
    source = Service(tmp_path / "source", read_roots=[FIXTURES])
    project, _, review = reviewed_regression_plan(source, "http://127.0.0.1:34567")
    package = copy.deepcopy(export_review(source, project, review))
    config = next(iter(package["assets"]["configs"].values()))
    config["environments"]["test"]["baseUrl"] = "http://different.example"
    destination = Service(tmp_path / "ci")
    with pytest.raises(HorusError, match="bundle_integrity"):
        import_review(destination, package)
    assert destination.list_projects() == []
