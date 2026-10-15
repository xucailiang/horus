from collections.abc import Iterator

from horus.core.models import BranchStep, HttpStep, LoadStep, PollStep, Step


def walk_steps(steps: list[Step], prefix: str = "") -> Iterator[tuple[str, Step]]:
    for step in steps:
        path = prefix + step.id
        yield path, step
        if isinstance(step, BranchStep):
            yield from walk_steps(step.thenSteps, path + ".then.")
            yield from walk_steps(step.elseSteps, path + ".else.")


def request_action(step: HttpStep | LoadStep | PollStep) -> HttpStep | LoadStep:
    return step.requestStep if isinstance(step, PollStep) else step


def selected_steps(
    steps: list[Step], selections: dict[str, str], prefix: str = "",
) -> Iterator[tuple[str, Step]]:
    for step in steps:
        path = prefix + step.id
        yield path, step
        if isinstance(step, BranchStep) and path in selections:
            side = selections[path]
            children = step.thenSteps if side == "then" else step.elseSteps
            yield from selected_steps(children, selections, path + "." + side + ".")
