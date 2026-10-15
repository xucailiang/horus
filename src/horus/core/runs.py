import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from horus.core.models import Plan, ResourceMetrics
from horus.core.reports import snapshot_test
from horus.core.service import Service
from horus.core.store import HorusError, atomic_write, digest, file_lock

TERMINAL = {"completed", "cancelled", "interrupted"}


class Runs:
    def __init__(self, service: Service) -> None:
        self.service = service

    def root(self, project_ref: str, run_id: str) -> Path:
        if not re.fullmatch(r"run_[0-9a-f]{32}", run_id):
            raise HorusError("invalid_run_id")
        root = self.service.project(project_ref).root / "runs" / run_id
        if not (root / "intent.json").is_file():
            raise HorusError("run_not_found")
        return root

    def start(
        self,
        project_ref: str,
        plan_ref: str,
        review_ref: str | None,
        environment: str,
        request_id: str,
        *,
        cleanup_from: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= len(request_id) <= 200:
            raise HorusError("invalid_request_id")
        store = self.service.project(project_ref)
        parameters = {"planRef": plan_ref, "reviewRef": review_ref, "environment": environment}
        if cleanup_from:
            original = self.get(project_ref, cleanup_from)
            if original["status"] not in TERMINAL:
                raise HorusError("run_not_finished")
            source = json.loads((self.root(project_ref, cleanup_from) / "intent.json").read_bytes())
            if source.get("cleanupFrom") or any(source[k] != v for k, v in parameters.items()):
                raise HorusError("cleanup_scope_mismatch")
            parameters["cleanupFrom"] = cleanup_from
        key = store.root / "requests" / f"{digest(request_id)}.json"
        with file_lock(store.root / ".start.lock"):
            if key.exists():
                registered = json.loads(key.read_bytes())
                if registered["parameters"] != parameters:
                    raise HorusError("request_conflict")
                return self.get(project_ref, registered["runId"])
            review = self.service.review_gate(project_ref, plan_ref, review_ref, environment)
            config = store.get("configs", review["configRef"])
            for name in config["environments"][environment]["secretBindings"].values():
                if name not in os.environ:
                    raise HorusError("missing_credential", name)
            run_id = "run_" + uuid.uuid4().hex
            root = store.root / "runs" / run_id
            intent = {
                **parameters,
                "projectRef": project_ref,
                "runId": run_id,
                "configRef": review["configRef"],
                "scopeHash": review["scopeHash"],
                "createdAt": time.time(),
            }
            intent.update(snapshot_test(self.service, root, intent))
            atomic_write(root / "intent.json", intent)
            atomic_write(root / "state.json", {**intent, "status": "queued"})
            atomic_write(key, {"parameters": parameters, "runId": run_id})
            with (root / "worker.log").open("ab") as log:
                os.chmod(root / "worker.log", 0o600)
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "horus.worker",
                        "--workspace",
                        str(self.service.workspace),
                        "--project",
                        project_ref,
                        "--run",
                        run_id,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=log,
                    start_new_session=True,
                    close_fds=True,
                    umask=0o077,
                )
            atomic_write(root / "process.json", {"pid": process.pid})
            threading.Thread(target=process.wait, daemon=True).start()
        # Handshake is bounded. A slow/missing handshake never triggers a second launch.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if (root / "heartbeat.json").exists() or process.poll() is not None:
                break
            time.sleep(0.02)
        return self.get(project_ref, run_id)

    def get(self, project_ref: str, run_id: str) -> dict[str, Any]:
        root = self.root(project_ref, run_id)
        state: dict[str, Any] = json.loads((root / "state.json").read_bytes())
        if state["status"] not in TERMINAL:
            heartbeat = root / "heartbeat.json"
            last = (
                json.loads(heartbeat.read_bytes())["at"]
                if heartbeat.exists()
                else state["createdAt"]
            )
            if time.time() - last > 30:
                process_file = root / "process.json"
                pid = (
                    json.loads(process_file.read_bytes())["pid"] if process_file.exists() else None
                )
                alive = False
                if pid:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except ProcessLookupError:
                        pass
                if not alive:
                    from horus.core.reports import interrupted_report

                    return interrupted_report(self.service, project_ref, run_id)
        return state

    async def wait(self, project_ref: str, run_id: str, wait_ms: int = 30000) -> dict[str, Any]:
        if not 0 <= wait_ms <= 30000:
            raise HorusError("invalid_wait")
        deadline = time.monotonic() + wait_ms / 1000
        while True:
            state = self.get(project_ref, run_id)
            if state["status"] in TERMINAL or time.monotonic() >= deadline:
                return state
            await asyncio.sleep(0.1)

    def cancel(self, project_ref: str, run_id: str) -> dict[str, Any]:
        root = self.root(project_ref, run_id)
        state = self.get(project_ref, run_id)
        if state["status"] not in TERMINAL:
            atomic_write(root / "cancel.json", {"at": time.time()})
        return {"runId": run_id, "status": "cancel_requested"}

    def cleanup(self, project_ref: str, run_id: str, request_id: str) -> dict[str, Any]:
        """Run only the original approved cleanup with recoverable identifiers and fresh auth."""
        intent = json.loads((self.root(project_ref, run_id) / "intent.json").read_bytes())
        return self.start(
            project_ref,
            intent["planRef"],
            intent["reviewRef"],
            intent["environment"],
            request_id,
            cleanup_from=run_id,
        )

    def attach_metrics(
        self, project_ref: str, run_id: str, metrics: ResourceMetrics
    ) -> dict[str, Any]:
        root = self.root(project_ref, run_id)
        with file_lock(root / ".report.lock"):
            if self.get(project_ref, run_id)["status"] not in TERMINAL:
                raise HorusError("run_not_finished")
            result = json.loads((root / "report.json").read_bytes())
            plan = Plan.model_validate(
                self.service.project(project_ref).get("plans", result["planRef"])
            )
            scope = plan.resourceMonitoring
            if (
                scope is None
                or metrics.targetLabel != scope.targetLabel
                or metrics.intervalSeconds < scope.intervalSeconds
                or (
                    metrics.samples
                    and metrics.samples[-1].at - metrics.samples[0].at
                    > scope.maxDurationSeconds + scope.intervalSeconds
                )
            ):
                raise HorusError("monitoring_scope_mismatch")
            data = metrics.model_dump(mode="json")
            if result.get("resourceMetrics") == data:
                return self.get(project_ref, run_id)
            result["resourceMetrics"] = data
            from horus.core.reports import write_report

            return write_report(self.service, root, result)
