"""Stop an engine when its worker's pipe closes; retain the project lease until exit."""

import os
import select
import signal
import subprocess
import sys


def supervise(lifetime_fd: int, command: list[str]) -> int:
    stopping = False

    def stop(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # A worker may disappear between spawning us and our first instruction.
    if select.select([lifetime_fd], [], [], 0)[0] and not os.read(lifetime_fd, 1):
        return 125
    engine = subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        while engine.poll() is None:
            ready, _, _ = select.select([lifetime_fd], [], [], 0.05)
            if stopping or (ready and not os.read(lifetime_fd, 1)):
                return 125
        return engine.returncode
    finally:
        if engine.poll() is None:
            try:
                os.killpg(engine.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        engine.wait()


if __name__ == "__main__":
    sys.exit(supervise(int(sys.argv[1]), sys.argv[2:]))
