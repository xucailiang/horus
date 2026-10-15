import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from horus.adapters.process import EXECUTION_LEASE
from horus.core.runner import Runner, write_report
from horus.core.runs import Runs
from horus.core.service import Service
from horus.core.store import atomic_write, file_lock


async def execute(workspace: Path, project: str, run_id: str) -> None:
    service = Service(workspace)
    root = Runs(service).root(project, run_id)

    async def heartbeat() -> None:
        while True:
            atomic_write(root / "heartbeat.json", {"pid": os.getpid(), "at": time.time()})
            await asyncio.sleep(2)

    heart = asyncio.create_task(heartbeat())

    def finish_problem(cancelled: bool, reason: str) -> None:
        intent = json.loads((root / "intent.json").read_bytes())
        write_report(
            service,
            root,
            {
                **intent,
                "status": "cancelled" if cancelled else "completed",
                "cases": [
                    {
                        "id": "platform",
                        "steps": [
                            {
                                "id": "worker",
                                "status": "inconclusive" if cancelled else "error",
                                "reason": reason,
                            }
                        ],
                        "cleanup": [],
                    }
                ],
            },
        )

    async def cancellation(execution: asyncio.Task[dict[str, Any]], runner: Runner) -> None:
        while not execution.done():
            if (root / "cancel.json").exists():
                runner.cancelled = True
                # Finish bounded HTTP requests so successful writes can be captured for cleanup.
                # Load processes and preflight can stop immediately.
                if runner.active_load or runner.phase == "queued":
                    execution.cancel()
                return
            await asyncio.sleep(0.1)

    try:
        with file_lock(root / ".claim.lock", blocking=False):
            if Runs(service).get(project, run_id)["status"] in {
                "completed",
                "cancelled",
                "interrupted",
            }:
                return
            while True:
                if (root / "cancel.json").exists():
                    finish_problem(True, "cancelled_before_start")
                    return
                try:
                    with file_lock(
                        service.project(project).root / ".execution.lock", blocking=False
                    ) as lease:
                        EXECUTION_LEASE.set(lease)
                        runner = Runner(service, project, run_id, root)
                        execution = asyncio.create_task(runner.execute())

                        watcher = asyncio.create_task(cancellation(execution, runner))
                        try:
                            await execution
                        finally:
                            watcher.cancel()
                        return
                except BlockingIOError:
                    await asyncio.sleep(0.1)
    except (asyncio.CancelledError, Exception) as exc:
        finish_problem(isinstance(exc, asyncio.CancelledError), type(exc).__name__)
    finally:
        heart.cancel()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    asyncio.run(execute(args.workspace, args.project, args.run))
