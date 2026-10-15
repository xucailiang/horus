import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import yaml
from openapi_spec_validator import validate

from horus.adapters.openapi import load_api
from horus.core.models import Plan, ProjectConfig
from horus.core.presentation import plan_document
from horus.core.store import AssetStore, HorusError, atomic_write, canonical, digest, file_lock
from horus.core.validation import FlowValidator


class Service:
    def __init__(self, workspace: Path, *, read_roots: list[Path] | None = None) -> None:
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.read_roots = [self.workspace, *(p.resolve() for p in (read_roots or []))]

    def project(self, project_ref: str) -> AssetStore:
        if not re.fullmatch(r"project_[0-9a-f]{32}", project_ref):
            raise HorusError("invalid_project_ref")
        root = self.workspace / "projects" / project_ref
        if not (root / "project.json").is_file():
            raise HorusError("project_not_found", project_ref)
        return AssetStore(root / ".horus")

    def list_projects(self) -> list[dict[str, Any]]:
        return [
            json.loads(p.read_bytes())
            for p in sorted((self.workspace / "projects").glob("*/project.json"))
        ]

    def open_project(self, name: str, *, create_if_missing: bool = False) -> dict[str, Any]:
        with file_lock(self.workspace / ".projects.lock"):
            matches = [p for p in self.list_projects() if name in (p["name"], p["projectRef"])]
            if len(matches) > 1:
                raise HorusError("ambiguous_project")
            if matches:
                result = matches[0]
            elif create_if_missing and 0 < len(name.strip()) <= 100:
                result = {"projectRef": "project_" + uuid.uuid4().hex, "name": name}
                atomic_write(
                    self.workspace / "projects" / result["projectRef"] / "project.json", result
                )
            else:
                raise HorusError("project_not_found", name)
        store = self.project(result["projectRef"])
        result["refs"] = {
            kind: [p.stem for p in sorted((store.root / kind).glob("*.json"))]
            for kind in ["sources", "configs", "plans", "bundles", "reviews"]
        }
        result["refs"]["runs"] = [
            path.parent.name for path in sorted((store.root / "runs").glob("*/intent.json"))
        ][-20:]
        return result

    def stage_config(self, project_ref: str, raw: dict[str, Any]) -> dict[str, Any]:
        config = ProjectConfig.model_validate(raw)
        for env in config.environments.values():
            target = env.baseUrl or os.environ.get(env.baseUrlFromEnv or "")
            if not target:
                raise HorusError("missing_target", env.baseUrlFromEnv or "baseUrl")
            parsed = urlsplit(target)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or any(c.isspace() for c in target)
                or "\\" in target
            ):
                raise HorusError("invalid_target", "Use an HTTP origin and optional base path")
            env.baseUrl = target.rstrip("/")
            env.baseUrlFromEnv = None
            for binding in env.secretBindings.values():
                if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", binding):
                    raise HorusError("invalid_secret_binding", "Use environment variable names")
        value = config.model_dump(mode="json", exclude_none=True)
        ref = self.project(project_ref).put("configs", value)
        return {"configRef": ref, "status": "draft", "config": value}

    def register_source(self, project_ref: str, kind: str, uri: str) -> dict[str, Any]:
        if kind == "openapi" and urlsplit(uri).scheme in {"http", "https"}:
            return self.import_api(project_ref, uri)
        if kind not in {"openapi", "markdown"}:
            raise HorusError("unsupported_source", kind)
        path = Path(uri).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        path = path.resolve()
        if not any(path.is_relative_to(root) for root in self.read_roots):
            raise HorusError("read_root_required", "Register this read root when starting MCP")
        if any(part in {".git", ".env", ".ssh"} for part in path.parts):
            raise HorusError("source_path_denied")
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise HorusError("source_unavailable", "Expected a UTF-8 file up to 2 MiB")
        text = path.read_text(encoding="utf-8")
        return self._save_source(project_ref, kind, path.name, text)

    def import_api(self, project_ref: str, url: str) -> dict[str, Any]:
        self.project(project_ref)
        text, resolved = load_api(url)
        return self._save_source(project_ref, "openapi", urlsplit(resolved).path, text)

    def _save_source(self, project_ref: str, kind: str, name: str, text: str) -> dict[str, Any]:
        catalog: dict[str, Any] = {}
        if kind == "openapi":
            spec = yaml.safe_load(text)
            if not isinstance(spec, dict):
                raise HorusError("invalid_openapi")
            self._local_refs_only(spec)
            validate(spec)
            for route, item in spec.get("paths", {}).items():
                for method, operation in item.items():
                    if method.lower() not in {
                        "get",
                        "post",
                        "put",
                        "patch",
                        "delete",
                        "head",
                        "options",
                    }:
                        continue
                    operation_id = operation.get("operationId", f"{method.upper()}:{route}")
                    if operation_id in catalog:
                        raise HorusError("duplicate_operation", operation_id)
                    catalog[operation_id] = {
                        "method": method.upper(),
                        "path": route,
                        "definition": operation,
                        "parameters": item.get("parameters", []) + operation.get("parameters", []),
                    }
        value = {"kind": kind, "name": name, "text": text, "catalog": catalog}
        ref = self.project(project_ref).put("sources", value)
        return {
            "sourceRef": ref,
            "status": "ready",
            "operationIds": list(catalog),
            "evidenceRef": f"{ref}#L1",
        }

    def _local_refs_only(self, value: Any) -> None:
        if isinstance(value, dict):
            if "$ref" in value and not str(value["$ref"]).startswith("#/"):
                raise HorusError("external_ref_unsupported", "Register a self-contained document")
            for child in value.values():
                self._local_refs_only(child)
        elif isinstance(value, list):
            for child in value:
                self._local_refs_only(child)

    def get_context(
        self, project_ref: str, source_ref: str, offset: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        source = self.project(project_ref).get("sources", source_ref)
        lines = source["text"].splitlines()
        if offset < 0 or not 1 <= limit <= 200:
            raise HorusError("invalid_page")
        return {
            "sourceRef": source_ref,
            "status": "ready",
            "catalog": source["catalog"],
            "lines": [
                {"line": i + 1, "text": line}
                for i, line in enumerate(lines[offset : offset + limit], start=offset)
            ],
            "nextOffset": offset + limit if offset + limit < len(lines) else None,
        }

    def catalog(self, store: AssetStore, plan: Plan) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for ref in plan.sourceRefs:
            source = store.get("sources", ref)
            for key, operation in source["catalog"].items():
                if key in result:
                    raise HorusError("ambiguous_operation", key)
                result[key] = operation
        return result

    def validate_plan(
        self,
        project_ref: str,
        plan_ref: str,
        config_ref: str | None = None,
        environment: str = "test",
    ) -> dict[str, Any]:
        store = self.project(project_ref)
        plan = Plan.model_validate(store.get("plans", plan_ref))
        config = (
            ProjectConfig.model_validate(store.get("configs", config_ref)) if config_ref else None
        )
        return self._validate(store, plan, config, environment)

    def _validate(
        self,
        store: AssetStore,
        plan: Plan,
        config: ProjectConfig | None = None,
        environment: str = "test",
    ) -> dict[str, Any]:
        operations = self.catalog(store, plan)
        sources = {ref: store.get("sources", ref) for ref in plan.sourceRefs}
        context_ids = {f"context:{item.id}" for item in plan.supportingContext}
        if len(context_ids) != len(plan.supportingContext):
            raise HorusError("duplicate_context_id")
        for match in re.finditer(r'"evidenceRefs":\s*(\[[^\]]*\])', plan.model_dump_json()):
            for ref in json.loads(match[1]):
                if ref in context_ids:
                    continue
                source_ref, _, locator = ref.partition("#")
                if source_ref not in sources or not re.fullmatch(
                    r"L[1-9][0-9]*(?:-L?[1-9][0-9]*)?", locator
                ):
                    raise HorusError("invalid_evidence", ref)
                numbers = [int(n) for n in re.findall(r"\d+", locator)]
                if max(numbers) > len(sources[source_ref]["text"].splitlines()):
                    raise HorusError("invalid_evidence", ref)
        profiles = {profile.id: profile for profile in plan.identityProfiles}
        if len(profiles) != len(plan.identityProfiles):
            raise HorusError("duplicate_identity")
        env = config.environments.get(environment) if config else None
        if config and env is None:
            raise HorusError("unknown_environment", environment)
        if env:
            for profile in profiles.values():
                if not set(profile.credentialRefs.values()).issubset(env.secretBindings):
                    raise HorusError("missing_identity_binding", profile.id)
        execution = FlowValidator(plan, operations, config, env).validate()
        case_ids = execution.case_ids
        assertion_refs = execution.assertion_refs
        capture_refs = execution.capture_refs
        step_refs = execution.step_refs
        max_requests = execution.max_requests
        declared_duration_ms = execution.declared_duration_ms
        understanding = plan.businessUnderstanding
        for claim in [*understanding.flows, *understanding.rules]:
            if not set(claim.operationIds).issubset(operations):
                raise HorusError("unknown_business_operation", claim.id)
        uncovered = {item.itemId for item in plan.coverage.uncovered}
        self._coverage(
            {f.id for f in understanding.flows}, {f.flowId for f in plan.coverage.flows}, uncovered
        )
        self._coverage(
            {r.id for r in understanding.rules}, {r.ruleId for r in plan.coverage.rules}, uncovered
        )
        self._coverage(
            {d.id for d in understanding.dataDependencies},
            {d.dependencyId for d in plan.coverage.dataDependencies},
            uncovered,
        )
        for flow in plan.coverage.flows:
            if not set(flow.caseIds).issubset(case_ids):
                raise HorusError("invalid_flow_coverage", flow.flowId)
        for rule in plan.coverage.rules:
            if not set(rule.assertionRefs).issubset(assertion_refs):
                invalid = sorted(set(rule.assertionRefs) - assertion_refs)
                raise HorusError(
                    "invalid_rule_coverage",
                    f"{rule.ruleId}: unknown {invalid}; use caseId.stepId.assertionId",
                )
        for dep in plan.coverage.dataDependencies:
            if dep.captureRef not in capture_refs or not set(dep.consumerStepRefs).issubset(
                step_refs
            ):
                raise HorusError("invalid_dependency_coverage", dep.dependencyId)
        if config:
            if environment not in config.environments:
                raise HorusError("unknown_environment", environment)
            if max_requests > config.policy.limits.maxRequestsPerRun:
                raise HorusError("request_budget_exceeded")
            if declared_duration_ms > config.policy.limits.maxRunDurationMs:
                raise HorusError("run_duration_budget_exceeded")
        return {
            "status": "validated",
            "maxRequests": max_requests,
            "operationIds": sorted(operations),
        }

    @staticmethod
    def _coverage(expected: set[str], mapped: set[str], uncovered: set[str]) -> None:
        if mapped - expected or expected - mapped - uncovered:
            raise HorusError("incomplete_coverage", str(sorted(expected - mapped - uncovered)))

    def preview_plan(
        self, project_ref: str, raw: dict[str, Any], config_ref: str, environment: str
    ) -> dict[str, Any]:
        """Validate and render in memory. No plan, review or test directory is written."""
        plan = Plan.model_validate(raw)
        store = self.project(project_ref)
        if plan.parentPlanRef:
            store.get("plans", plan.parentPlanRef)
        config = ProjectConfig.model_validate(store.get("configs", config_ref))
        validation = self._validate(store, plan, config, environment)
        scope = {
            "projectRef": project_ref,
            "configRef": config_ref,
            "environment": environment,
            "plan": plan.model_dump(mode="json"),
        }
        return {
            "previewRef": "preview_" + digest(scope),
            "status": "preview",
            "content": plan_document(plan, config, environment, validation),
            "validation": validation,
        }

    def confirm_plan(
        self,
        project_ref: str,
        raw: dict[str, Any],
        config_ref: str,
        environment: str,
        preview_ref: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Persist the unchanged preview only after a real human confirmation in chat."""
        preview = self.preview_plan(project_ref, raw, config_ref, environment)
        if preview["previewRef"] != preview_ref:
            raise HorusError("preview_mismatch", "Display the changed draft for fresh confirmation")
        if not confirmation.strip():
            raise HorusError("invalid_review_decision")
        plan_ref = self.save_plan(project_ref, raw)["planRef"]
        bundle = self.prepare_review(project_ref, plan_ref, config_ref, environment,
                                     preview=preview)
        store = self.project(project_ref)
        bundle_ref = bundle["reviewBundleRef"]
        # The host displayed the in-memory preview before receiving this confirmation.
        atomic_write(
            store.root / "presented" / f"{bundle['artifactRef']}.json",
            {"confirmedAt": time.time(), "previewRef": preview_ref, "via": "conversation_preview"},
        )
        review = self.record_decision(
            project_ref,
            bundle_ref,
            plan_ref,
            bundle["scopeHash"],
            "approved",
            "conversation",
            confirmation,
        )
        return {
            **review,
            "planRef": plan_ref,
            "reviewBundleRef": bundle_ref,
            "previewRef": preview_ref,
        }

    def save_plan(self, project_ref: str, raw: dict[str, Any]) -> dict[str, Any]:
        plan = Plan.model_validate(raw)
        store = self.project(project_ref)
        if plan.parentPlanRef:
            store.get("plans", plan.parentPlanRef)
        result = self._validate(store, plan)
        ref = store.put("plans", plan.model_dump(mode="json"))
        return {**result, "planRef": ref}

    def get_plan(
        self, project_ref: str, plan_ref: str, offset: int = 0, limit: int = 200,
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 200:
            raise HorusError("invalid_page")
        plan = self.project(project_ref).get("plans", plan_ref)
        lines = json.dumps(plan, ensure_ascii=False, indent=2).split("\n")
        return {
            "planRef": plan_ref, "offset": offset,
            "content": "\n".join(lines[offset:offset + limit]),
            "nextOffset": offset + limit if offset + limit < len(lines) else None,
        }

    def prepare_review(
        self, project_ref: str, plan_ref: str, config_ref: str, environment: str,
        *, preview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        store = self.project(project_ref)
        validation = self.validate_plan(project_ref, plan_ref, config_ref, environment)
        config = store.get("configs", config_ref)
        scope = {
            "projectRef": project_ref,
            "environment": environment,
            "target": config["environments"][environment]["baseUrl"],
            "policy": config["policy"],
        }
        bundle = {
            "planRef": plan_ref,
            "configRef": config_ref,
            "environment": environment,
            "scopeHash": digest(scope),
            "scope": scope,
            "validation": validation,
        }
        plan = Plan.model_validate(store.get("plans", plan_ref)).model_dump(
            mode="json", exclude_defaults=True, exclude_none=True
        )
        content = (
            f"# Review {plan['name']}\n\nPlan: {plan_ref}\n\nConfig: {config_ref}\n\n"
            f"Target: {scope['target']}\n\nScope: {bundle['scopeHash']}\n\n"
            f"{plan['businessUnderstanding']['summary']}\n\n"
            "## Scope and budgets\n\n```json\n"
            + json.dumps(scope, indent=2)
            + "\n```\n\n## Complete executable plan\n\n```json\n"
            + json.dumps(plan, ensure_ascii=False, indent=2)
            + "\n```\n"
        )
        artifact_ref = store.put("artifacts", {"kind": "review", "content": content})
        bundle["artifactRef"] = artifact_ref
        if preview is not None:
            bundle.update(previewRef=preview["previewRef"], previewContent=preview["content"])
        bundle_ref = store.put("bundles", bundle)
        return {**bundle, "reviewBundleRef": bundle_ref, "status": "awaiting_review"}

    def get_artifact(
        self, project_ref: str, artifact_ref: str, offset: int = 0, limit: int = 200
    ) -> dict[str, Any]:
        store = self.project(project_ref)
        asset = store.get("artifacts", artifact_ref)
        if offset < 0 or not 1 <= limit <= 200:
            raise HorusError("invalid_page")
        lines = asset["content"].split("\n")
        if asset["kind"] == "review":
            atomic_write(store.root / "presented" / f"{artifact_ref}.json", {"readAt": time.time()})
        return {
            "artifactRef": artifact_ref,
            "kind": asset["kind"],
            "offset": offset,
            "content": "\n".join(lines[offset : offset + limit]),
            "nextOffset": offset + limit if offset + limit < len(lines) else None,
        }

    def record_decision(
        self,
        project_ref: str,
        bundle_ref: str,
        plan_ref: str,
        scope_hash: str,
        decision: str,
        source: str,
        confirmation: str,
    ) -> dict[str, Any]:
        store = self.project(project_ref)
        bundle = store.get("bundles", bundle_ref)
        if bundle["planRef"] != plan_ref or bundle["scopeHash"] != scope_hash:
            raise HorusError("review_mismatch")
        if not (store.root / "presented" / f"{bundle['artifactRef']}.json").is_file():
            raise HorusError("review_not_presented")
        if (
            decision not in {"approved", "rejected"}
            or source not in {"conversation", "CLI", "test-only"}
            or not confirmation.strip()
        ):
            raise HorusError("invalid_review_decision")
        record = {
            **bundle,
            "reviewBundleRef": bundle_ref,
            "decision": decision,
            "source": source,
            "confirmation": confirmation,
            "recordedAt": time.time(),
            "trust": "test-only" if source == "test-only" else "local_recorded",
        }
        ref = store.put("reviews", record)
        return {"reviewRef": ref, "status": decision, "trust": record["trust"]}

    def review_gate(
        self, project_ref: str, plan_ref: str, review_ref: str | None, environment: str
    ) -> dict[str, Any]:
        if not review_ref:
            raise HorusError("review_required")
        store = self.project(project_ref)
        record = store.get("reviews", review_ref)
        if record["decision"] != "approved":
            raise HorusError("review_required")
        if record["planRef"] != plan_ref or record["environment"] != environment:
            raise HorusError("review_mismatch")
        if (store.root / "revoked" / f"{review_ref}.json").exists():
            raise HorusError("review_revoked")
        if record["trust"] == "test-only" and os.environ.get("HORUS_TEST_MODE") != "1":
            raise HorusError("test_review_not_allowed", "Only isolated regression tests may use it")
        bundle = store.get("bundles", record["reviewBundleRef"])
        if canonical({k: record[k] for k in bundle}) != canonical(bundle):
            raise HorusError("review_mismatch")
        self.validate_plan(project_ref, plan_ref, record["configRef"], environment)
        config = store.get("configs", record["configRef"])
        expected_scope = {
            "projectRef": project_ref,
            "environment": environment,
            "target": config["environments"][environment]["baseUrl"],
            "policy": config["policy"],
        }
        if digest(expected_scope) != record["scopeHash"]:
            raise HorusError("review_mismatch", "Approval belongs to a different project or scope")
        return cast(dict[str, Any], record)
