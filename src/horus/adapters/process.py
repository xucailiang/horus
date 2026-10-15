import asyncio
import os
import shutil
import signal
from pathlib import Path

from horus.core.store import HorusError

VERSIONS = {"hurl": "8.0.1", "k6": "2.3.0"}
MAX_OUTPUT = 2 * 1024 * 1024


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
    process = await asyncio.create_subprocess_exec(
        engine_path(name),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": os.defpath, **(env or {})},
        start_new_session=True,
        umask=0o077,
    )

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
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        await asyncio.gather(stdout, stderr)
        raise
    output, errors = await asyncio.gather(stdout, stderr)
    if len(output) > MAX_OUTPUT or len(errors) > MAX_OUTPUT:
        raise HorusError("engine_output_truncated", name)
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
