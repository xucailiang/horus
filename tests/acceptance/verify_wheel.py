"""Exercise a built base wheel with frozen dependencies, without MCP or model clients."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from horus.adapters.process import engine_path
from horus.core.bundles import export_review
from horus.core.service import Service
from horus.core.store import atomic_write
from tests.fixtures.reference_api import ReferenceServer
from tests.support import FIXTURES, reviewed_regression_plan


def main() -> None:
    repository = Path(__file__).resolve().parents[2]
    wheel = next((repository / "dist").glob("*.whl"))
    uv = shutil.which("uv")
    assert uv
    # Approval here is explicitly test-only, never actual human acceptance.
    os.environ["HORUS_TEST_MODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="horus-wheel-") as temporary:
        root = Path(temporary)
        requirements = root / "requirements.txt"
        subprocess.run([
            uv, "export", "--frozen", "--no-dev", "--no-emit-project", "--quiet",
            "--output-file", str(requirements),
        ], cwd=repository, check=True, stdout=subprocess.DEVNULL)
        subprocess.run([uv, "venv", "--python", "3.12", str(root / "venv")], check=True)
        python = root / "venv" / "bin" / "python"
        subprocess.run([
            uv, "pip", "sync", "--python", str(python), "--require-hashes", str(requirements),
        ], check=True)
        subprocess.run([
            uv, "pip", "install", "--python", str(python), "--no-deps", str(wheel),
        ], check=True)
        env = {
            "PATH": os.defpath,
            "HORUS_TEST_MODE": "1",
            "HORUS_USERNAME": "fixture-user",
            "HORUS_PASSWORD": "private-wheel-fixture",
            "HORUS_HURL_BIN": engine_path("hurl"),
            "HORUS_K6_BIN": engine_path("k6"),
        }
        subprocess.run([
            str(python), "-c",
            "import importlib.util; from pathlib import Path; import horus; "
            "assert importlib.util.find_spec('fastmcp') is None; "
            "p=Path(horus.__file__).parent; "
            "assert (p/'adapters/load.js').is_file(); "
            "assert (p/'adapters/guardian.py').is_file(); "
            "assert (p/'skill/SKILL.md').is_file(); "
            "assert (p/'skill/references/resource-monitoring.md').is_file()",
        ], env=env, cwd=root, check=True)
        for mode, code, verdict in [("normal", 0, "passed"), ("data-error", 1, "failed")]:
            server = ReferenceServer(
                0, mode=mode, username=env["HORUS_USERNAME"], password=env["HORUS_PASSWORD"]
            )
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                service = Service(root / f"source-{mode}", read_roots=[FIXTURES])
                project, plan, review = reviewed_regression_plan(
                    service, f"http://127.0.0.1:{server.server_port}"
                )
                package = root / f"{mode}.json"
                atomic_write(package, export_review(service, project, review))
                workspace = root / f"ci-{mode}"
                command = [str(python), "-m", "horus"]
                subprocess.run(command + [
                    "plan", "import", "--workspace", str(workspace), "--bundle", str(package),
                ], env=env, cwd=root, check=True, stdout=subprocess.DEVNULL)
                result = subprocess.run(command + [
                    "run", "--workspace", str(workspace), "--project-ref", project,
                    "--plan-ref", plan, "--review-ref", review,
                ], env=env, cwd=root, capture_output=True, timeout=40)
                assert result.returncode == code, result.stderr.decode(errors="replace")
                summary = json.loads(result.stdout)
                assert summary["verdict"] == verdict, summary
                assert not server.records and not server.tokens
                for path in workspace.rglob("*"):
                    if path.is_file():
                        assert env["HORUS_PASSWORD"] not in path.read_text(errors="replace")
                print(f"base wheel: {mode} → {verdict}, exit {code}; cleanup verified")
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    main()
