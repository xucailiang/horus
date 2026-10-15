import asyncio
import os
import shutil
import signal
import sys
from contextvars import ContextVar
from pathlib import Path

from horus.core.store import HorusError

VERSIONS = {"hurl": "8.0.1", "k6": "2.3.0"}
MAX_OUTPUT = 2 * 1024 * 1024
EXECUTION_LEASE: ContextVar[int | None] = ContextVar("execution_lease", default=None)


def engine_path(name: str) -> str:
    configured = os.environ.get(f"HORUS_{name.upper()}_BIN")
    bundled = Path(__file__).resolve().parents[3] / ".tools" / "bin" / name
    found = configured or shutil.which(name) or (str(bundled) if bundled.is_file() else None)
    if not found:
        raise HorusError("engine_missing", name)
    return found


async def run_engine(
    name: str, args: list[str], *, timeout: float, env: dict[str, str] | None = None
) -> tuple[int, bytes, bytes]:
    return await run_process(engine_path(name), args, timeout=timeout, env=env)


async def run_process(
    executable: str, args: list[str], *, timeout: float, env: dict[str, str] | None = None
) -> tuple[int, bytes, bytes]:
    lifetime_read, lifetime_write = os.pipe()
    lease = EXECUTION_LEASE.get()
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "horus.adapters.guardian",
            str(lifetime_read),
            executable,
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.defpath, **(env or {})},
            pass_fds=(lifetime_read,) if lease is None else (lifetime_read, lease),
            start_new_session=True,
            umask=0o077,
        )
    except BaseException:
        os.close(lifetime_write)
        raise
    finally:
        os.close(lifetime_read)

    async def drain(reader: asyncio.StreamReader | None) -> bytes:
        assert reader is not None
        collected = bytearray()
        while chunk := await reader.read(65536):
            collected.extend(chunk[: max(0, MAX_OUTPUT + 1 - len(collected))])
        return bytes(collected)

    stdout = asyncio.create_task(drain(process.stdout))
    stderr = asyncio.create_task(drain(process.stderr))
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except BaseException:
        os.close(lifetime_write)
        lifetime_write = -1
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                # SIGTERM asks the guardian to reap its engine before releasing the lease.
                try:
                    os.kill(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                await asyncio.wait_for(process.wait(), timeout=2)
        await asyncio.gather(stdout, stderr)
        raise
    finally:
        if lifetime_write >= 0:
            os.close(lifetime_write)
    output, errors = await asyncio.gather(stdout, stderr)
    if len(output) > MAX_OUTPUT or len(errors) > MAX_OUTPUT:
        raise HorusError("engine_output_truncated", Path(executable).name)
    return process.returncode or 0, output, errors


async def check_engine(name: str) -> str:
    code, output, _ = await run_engine(
        name, ["--version" if name == "hurl" else "version"], timeout=5
    )
    version = output.decode(errors="replace").splitlines()[0]
    expected = f"hurl {VERSIONS[name]} " if name == "hurl" else f"k6 v{VERSIONS[name]} "
    if code or not version.startswith(expected):
        raise HorusError("engine_version_mismatch", f"{name} requires {VERSIONS[name]}")
    return version
