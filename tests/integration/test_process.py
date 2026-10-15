import asyncio
import os
import signal
import sys

import pytest

from horus.core.store import file_lock


async def test_worker_kill_stops_engine_and_releases_lease_only_after_exit(tmp_path):
    marker, lease = tmp_path / "engine-pid", tmp_path / "project.lock"
    engine = "\n".join([
        "import os,time",
        f"open({str(marker)!r}, 'w').write(str(os.getpid()))",
        "time.sleep(30)",
    ])
    program = "\n".join([
        "import asyncio,os,sys",
        "from pathlib import Path",
        "from horus.adapters.process import EXECUTION_LEASE,run_engine",
        "from horus.core.store import file_lock",
        "os.environ['HORUS_HURL_BIN'] = sys.executable",
        f"with file_lock(Path({str(lease)!r})) as fd:",
        "    EXECUTION_LEASE.set(fd)",
        f"    asyncio.run(run_engine('hurl', ['-c', {engine!r}], timeout=40))",
    ])
    parent = await asyncio.create_subprocess_exec(sys.executable, "-c", program)
    engine_pid = None
    try:
        for _ in range(100):
            if marker.exists() and marker.read_text():
                engine_pid = int(marker.read_text())
                break
            await asyncio.sleep(0.05)
        assert engine_pid is not None
        with pytest.raises(BlockingIOError), file_lock(lease, blocking=False):
            pass
        parent.kill()
        await parent.wait()
        for _ in range(60):
            try:
                os.kill(engine_pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("engine survived its worker")
        with file_lock(lease, blocking=False):
            pass
    finally:
        if parent.returncode is None:
            parent.kill()
            await parent.wait()
        if engine_pid is not None:
            try:
                os.killpg(engine_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
