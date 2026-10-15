"""ECMAScript matching isolated from the Worker, using the engine guardian's lifetime lease."""

import json
import sys
import tempfile
from pathlib import Path

from regress import Regex

from horus.adapters.process import run_process
from horus.core.store import HorusError


async def match_patterns(inputs: list[tuple[str, str]]) -> list[bool]:
    if not inputs:
        return []
    with tempfile.TemporaryDirectory(prefix="horus-private-regex-") as directory:
        path = Path(directory) / "input.json"
        path.write_text(json.dumps(inputs))
        path.chmod(0o600)
        try:
            code, output, _ = await run_process(
                sys.executable, ["-m", __name__, str(path)], timeout=1
            )
        except TimeoutError as exc:
            raise HorusError("regex_timeout") from exc
    if code:
        raise HorusError("regex_engine_error")
    matches = json.loads(output)
    if (
        not isinstance(matches, list)
        or len(matches) != len(inputs)
        or any(type(value) is not bool for value in matches)
    ):
        raise HorusError("invalid_regex_result")
    return matches


if __name__ == "__main__":
    inputs = json.loads(Path(sys.argv[1]).read_bytes())
    print(
        json.dumps([Regex(pattern, flags="u").find(value) is not None for pattern, value in inputs])
    )
