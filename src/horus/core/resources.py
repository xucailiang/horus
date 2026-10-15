from horus.core.models import ResourceMetrics
from horus.core.presentation import markdown_text


def resource_summary(metrics: ResourceMetrics, text: dict[str, str]) -> list[str]:
    lines = [
        "",
        "## " + text["monitoring"],
        "",
        markdown_text(metrics.targetLabel) + " · " + text.get(metrics.status, metrics.status),
        "",
        "| " + text["results"] + " | " + text["peak"] + " |",
        "| --- | --- |",
    ]

    def row(name: str, values: list[float | int], unit: str, scale: float = 1) -> None:
        if values:
            lines.append(f"| {name} | {max(values) / scale:.2f} {unit} |")

    row("CPU", [s.cpuPercent for s in metrics.samples if s.cpuPercent is not None], "%")
    row(
        text["memory"],
        [s.memoryUsedBytes for s in metrics.samples if s.memoryUsedBytes is not None],
        "MiB",
        1024**2,
    )
    for index in sorted({gpu.index for sample in metrics.samples for gpu in sample.gpus}):
        samples = [gpu for sample in metrics.samples for gpu in sample.gpus if gpu.index == index]
        row(
            f"GPU {index}",
            [s.utilizationPercent for s in samples if s.utilizationPercent is not None],
            "%",
        )
        row(
            f"GPU {index} · " + text["memory"],
            [s.memoryUsedBytes for s in samples if s.memoryUsedBytes is not None],
            "MiB",
            1024**2,
        )
    return [*lines, "", *(markdown_text(note) for note in metrics.notes)]
