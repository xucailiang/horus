import json
from copy import deepcopy

import pytest

from horus.core.reports import write_report
from horus.core.store import HorusError
from tests.unit.test_execution_safety import prepared_runner
from tests.unit.test_review import project  # noqa: F401


def test_preview_is_read_only_and_confirmation_binds_exact_draft(project):  # noqa: F811
    service, project_ref, plan, config = project
    plan["report"] = {"language": "zh-CN"}
    plan["cases"][0]["title"] = "查看记录列表"
    config_ref = service.stage_config(project_ref, config)["configRef"]
    before = sorted(p.relative_to(service.workspace) for p in service.workspace.rglob("*"))
    preview = service.preview_plan(project_ref, plan, config_ref, "test")
    after = sorted(p.relative_to(service.workspace) for p in service.workspace.rglob("*"))
    assert before == after
    assert "查看记录列表" in preview["content"]
    assert "http://127.0.0.1:34567" in preview["content"]
    changed = deepcopy(plan)
    changed["cases"][0]["title"] = "更改后的计划"
    with pytest.raises(HorusError, match="preview_mismatch"):
        service.confirm_plan(
            project_ref, changed, config_ref, "test", preview["previewRef"], "用户消息：我确定"
        )
    assert not list(service.project(project_ref).root.glob("plans/*.json"))
    confirmed = service.confirm_plan(
        project_ref, plan, config_ref, "test", preview["previewRef"], "用户消息：我确定"
    )
    gate = service.review_gate(project_ref, confirmed["planRef"], confirmed["reviewRef"], "test")
    assert gate["confirmation"] == "用户消息：我确定"
    assert gate["previewRef"] == preview["previewRef"]
    assert gate["previewContent"] == preview["content"]
    assert not list(service.project(project_ref).root.glob("runs/*"))


def test_html_report_contains_plan_logic_localized_data_and_same_directory(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)
    plan = runner.plan.model_dump(mode="json")
    plan["report"] = {"language": "zh-CN"}
    plan["name"] = "记录业务验证"
    plan["cases"][0]["title"] = "创建后应能读回相同记录"
    plan["cases"][0]["steps"][0]["title"] = "登录并取得本轮凭据"
    plan["cases"][0]["steps"][2]["title"] = "读取并核对刚才写入的数值"
    plan["businessUnderstanding"]["summary"] = "先准备独立记录，再核对读取结果，最后清理。"
    plan["businessUnderstanding"]["unknowns"] = [
        {
            "id": "boundary",
            "description": '<script>alert("unsafe")</script>',
            "impact": "尚未覆盖边界条件",
            "evidenceRefs": [],
        }
    ]
    plan_ref = runner.service.save_plan(runner.project_ref, plan)["planRef"]
    summary = write_report(
        runner.service,
        runner.root,
        {
            **runner.intent,
            "planRef": plan_ref,
            "status": "completed",
            "cases": [
                {
                    "id": "lifecycle",
                    "steps": [
                        {
                            "id": "read",
                            "status": "failed",
                            "assertions": [
                                {
                                    "id": "value",
                                    "status": "failed",
                                    "actual": 43,
                                    "expected": 42,
                                }
                            ],
                            "response": {"status": 200, "body": {"value": 43}},
                        }
                    ],
                    "cleanup": [],
                }
            ],
        },
    )
    html = (runner.root / "report.html").read_text()
    assert '<html lang="zh-CN">' in html
    assert "业务与测试逻辑" in html and "测试数据" in html
    assert html.index('id="logic"') < html.index('id="results"')
    assert "登录并取得本轮凭据" in html  # Planned steps survive, even if never executed.
    assert "创建后应能读回相同记录" in html
    assert "未执行" in html and "未通过" in html
    assert "43" in html and "42" in html
    assert '<script>alert("unsafe")</script>' not in html
    assert "&lt;script&gt;" in html
    assert "<svg" in html and "data-filter" in html and "<details" in html
    assert "https://cdn" not in html and "<script src=" not in html
    assert json.loads((runner.root / "plan.json").read_text()) == runner.service.project(
        runner.project_ref
    ).get("plans", plan_ref)
    assert "记录业务验证" in (runner.root / "plan.md").read_text()
    assert summary["testDirectory"] == str(runner.root)
    assert summary["reportPath"] == str(runner.root / "report.html")
    artifact = runner.service.project(runner.project_ref).get(
        "artifacts", summary["htmlArtifactRef"]
    )
    assert artifact["content"] == html
    content = []
    offset = 0
    while offset is not None:
        page = runner.service.get_artifact(
            runner.project_ref, summary["htmlArtifactRef"], offset, 7)
        content.append(page["content"])
        offset = page["nextOffset"]
    assert "\n".join(content) == html


def test_report_locales_are_explicit_and_do_not_follow_machine_locale(monkeypatch):
    from pydantic import ValidationError

    from horus.core.models import ReportPresentation
    from horus.core.report_text import EN, labels

    monkeypatch.setenv('LANG', 'zh_CN.UTF-8')
    assert ReportPresentation().language == 'en'  # Old plans have a stable fallback.
    with pytest.raises(ValidationError, match='every report label'):
        ReportPresentation(language='ja')
    translated = {key: '訳文 ' + value for key, value in EN.items()}
    style = ReportPresentation(language='ja', labels=translated)
    assert labels(style.language, style.labels)['report'] == translated['report']


def test_html_preserves_both_paths_bounded_wait_and_not_selected_status(tmp_path):
    from horus.core.html_report import render_html
    from horus.core.service import Service
    from tests.branch_plan import reviewed_branch_plan
    from tests.support import FIXTURES

    service = Service(tmp_path, read_roots=[FIXTURES])
    project_ref, plan_ref, _ = reviewed_branch_plan(service, 'http://127.0.0.1:1')
    plan = service.project(project_ref).get('plans', plan_ref)
    plan['report'] = {'language': 'zh-CN'}
    case = plan['cases'][0]
    branch = next(s for s in case['steps'] if s['id'] == 'completion')
    html = render_html({
        'runId': 'run_' + 'a' * 32, 'verdict': 'inconclusive', 'cases': [{
            'id': case['id'], 'cleanup': [], 'steps': [{
                'id': 'completion', 'kind': 'branch', 'status': 'passed', 'selected': 'then',
                'steps': [{
                    'id': 'completion.else.wait', 'kind': 'poll', 'status': 'skipped',
                    'reason': 'not_selected',
                }],
            }],
        }],
    }, plan)
    assert '条件成立' in html and '条件不成立' in html
    assert branch['thenSteps'][0]['operationId'] in html
    assert '等待预期状态' in html and '未选择的分支' in html
    assert str(branch['elseSteps'][0]['maxAttempts']) in html
    assert '未执行' in html


def test_html_marks_large_evidence_excerpts_without_dropping_the_json_result(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)
    response = {"status": 200, "body": "evidence-prefix" + "x" * 30000 + "unretained-tail"}
    write_report(runner.service, runner.root, {
        **runner.intent, "status": "completed", "cases": [{
            "id": "lifecycle", "cleanup": [], "steps": [{
                "id": "read", "status": "failed", "response": response,
            }],
        }],
    })
    html = (runner.root / "report.html").read_text()
    assert "evidence-prefix" in html and "unretained-tail" not in html
    assert "truncated" in html and "report.json" in html
    assert json.loads((runner.root / "report.json").read_text())["cases"][0]["steps"][0][
        "response"] == response
