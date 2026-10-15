from datetime import UTC, datetime
from statistics import fmean

from horus.core.models import ResourceMetrics


def resource_summary(metrics: ResourceMetrics) -> list[str]:
    lines = [
        "",
        "## 机器资源监控",
        "",
        f"目标：{metrics.targetLabel}；来源：SSH，由宿主 Agent 提交；状态：{metrics.status}",
        "",
    ]
    if metrics.samples:
        start, end = (
            datetime.fromtimestamp(s.at, UTC).isoformat()
            for s in [metrics.samples[0], metrics.samples[-1]]
        )
        lines.extend(
            [
                f"时间：{start} — {end}；样本：{len(metrics.samples)}",
                "",
                "| 指标 | 均值 | 峰值 |",
                "| --- | ---: | ---: |",
            ]
        )

        def row(label: str, values: list[float | int], scale: float = 1) -> None:
            if values:
                lines.append(
                    f"| {label} | {fmean(values) / scale:.2f} | {max(values) / scale:.2f} |"
                )

        row("CPU (%)", [s.cpuPercent for s in metrics.samples if s.cpuPercent is not None])
        row(
            "内存 (MiB)",
            [s.memoryUsedBytes for s in metrics.samples if s.memoryUsedBytes is not None],
            1024**2,
        )
        gpu_ids = sorted({gpu.index for sample in metrics.samples for gpu in sample.gpus})
        for index in gpu_ids:
            samples = [gpu for s in metrics.samples for gpu in s.gpus if gpu.index == index]
            row(
                f"GPU {index} (%)",
                [s.utilizationPercent for s in samples if s.utilizationPercent is not None],
            )
            row(
                f"GPU {index} 显存 (MiB)",
                [s.memoryUsedBytes for s in samples if s.memoryUsedBytes is not None],
                1024**2,
            )
    lines.extend(["", "主机观测不等于服务进程的独占消耗；缺测不计作 0。", ""])
    lines.extend(f"- {note}" for note in metrics.notes)
    return lines
