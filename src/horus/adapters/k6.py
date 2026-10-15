import json
import math
import tempfile
from pathlib import Path
from typing import Any

from horus.adapters.hurl import build_url, validate_headers
from horus.adapters.process import MAX_OUTPUT, run_engine
from horus.core.models import LoadStep
from horus.core.store import HorusError


class K6Adapter:
    async def run(self, operation: dict[str, Any], base_url: str, step: LoadStep) -> dict[str, Any]:
        if any(a.jsonSchema is not None or a.elapsedMs for a in step.assertions):
            raise HorusError("unsupported_load_assertion")
        headers = dict(step.request.headers)
        if step.request.body is not None:
            headers.setdefault("Content-Type", "application/json")
        validate_headers(headers)
        with tempfile.TemporaryDirectory(prefix="horus-private-") as temporary:
            root = Path(temporary).resolve()
            summary = root / "summary.json"
            measurements = root / "measurements.jsonl"
            payload = {
                "url": build_url(operation, base_url, step.request),
                "method": operation["method"],
                "headers": headers,
                "body": step.request.body,
                "assertions": [a.model_dump(mode="json") for a in step.assertions],
                "profile": step.loadProfile.model_dump(mode="json"),
            }
            code, _, logs = await run_engine(
                "k6",
                [
                    "--log-format",
                    "json",
                    "run",
                    "--quiet",
                    "--new-machine-readable-summary",
                    "--out",
                    f"json={measurements}",
                    "--system-tags",
                    "status,method",
                    str(Path(__file__).with_name("load.js")),
                ],
                timeout=step.loadProfile.maxDurationMs / 1000 + 5,
                env={
                    "HORUS_K6_INPUT": json.dumps(payload),
                    "HORUS_SUMMARY_PATH": str(summary),
                    "XDG_CONFIG_HOME": str(root / "config"),
                    "K6_NO_USAGE_REPORT": "true",
                },
            )
            if code:
                raise HorusError("k6_engine_error", f"exit {code}")
            try:
                if summary.stat().st_size > MAX_OUTPUT:
                    raise HorusError("k6_summary_truncated")
                raw = json.loads(summary.read_bytes())
                if raw["version"] != "1.0.0" or raw["metadata"]["k6_version"] != "2.3.0":
                    raise HorusError("unsupported_k6_summary")
                metrics = {m["name"]: m["values"] for m in raw["results"]["metrics"]}
                duration = raw["config"]["duration"]
                latency = metrics["http_req_duration"]
                # k6 2.3's new summary has fixed med/p95 fields and cannot emit P99.
                # Read official JSON Point output, retaining only bounded timing samples.
                if measurements.stat().st_size > 64 * 1024 * 1024:
                    raise HorusError("k6_measurements_truncated")
                timings = []
                observed_vus = set()
                with measurements.open() as stream:
                    for line in stream:
                        point = json.loads(line)
                        if (
                            point.get("type") == "Point"
                            and point.get("metric") == "http_req_duration"
                        ):
                            timings.append(float(point["data"]["value"]))
                            observed_vus.add(point["data"]["tags"]["horus_vu"])
                timings.sort()
                if not timings:
                    raise HorusError("k6_no_samples")
                index = (len(timings) - 1) * 0.99
                p99 = (
                    timings[math.floor(index)] * (math.ceil(index) - index)
                    + timings[math.ceil(index)] * (index - math.floor(index))
                    if not index.is_integer()
                    else timings[int(index)]
                )
                normalized = {
                    "configuredVus": step.loadProfile.vus,
                    "observedVus": len(observed_vus),
                    "configuredIterations": step.loadProfile.iterations,
                    "completedIterations": metrics["iterations"]["count"],
                    "requests": metrics["http_reqs"]["count"],
                    "requestsPerSecond": metrics["http_reqs"]["count"] / duration,
                    "latencyAvgMs": latency["avg"],
                    "latencyP50Ms": latency["med"],
                    "latencyP95Ms": latency["p95"],
                    "latencyP99Ms": p99,
                    "latencyMaxMs": latency["max"],
                    "httpErrorRate": metrics["horus_http_failure"]["rate"],
                    "dataCheckFailureRate": metrics["horus_data_failure"]["rate"],
                    "sampleCount": metrics["horus_data_failure"]["total"],
                }
            except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
                raise HorusError("unsupported_k6_summary") from exc
            thresholds = []
            for name, threshold in step.thresholds.model_dump(exclude_none=True).items():
                actual = normalized[name]
                operator, expected = next(iter(threshold.items()))
                passed = actual <= expected if operator == "lte" else actual == expected
                thresholds.append(
                    {
                        "metric": name,
                        "actual": actual,
                        "operator": operator,
                        "expected": expected,
                        "status": "passed" if passed else "failed",
                    }
                )
            complete = (
                normalized["completedIterations"] == step.loadProfile.iterations
                and normalized["requests"] == step.loadProfile.iterations
                and normalized["sampleCount"] > 0
                and len(timings) == normalized["requests"]
            )
            samples = []
            for raw_line in logs.splitlines():
                try:
                    message = json.loads(raw_line).get("msg", "")
                    if message.startswith("HORUS_SAMPLE:"):
                        samples.append(json.loads(message.removeprefix("HORUS_SAMPLE:")))
                except (ValueError, TypeError):
                    continue
            if not complete or any(t["status"] == "failed" for t in thresholds):
                verdict = "failed"
            elif not thresholds or any(a.tentative for a in step.assertions):
                verdict = "inconclusive"
            else:
                verdict = "passed"
            return {
                "status": verdict,
                "metrics": normalized,
                "thresholds": thresholds,
                "failureSamples": samples[: 5 * step.loadProfile.vus],
                "samplingLimitPerVu": 5,
                "summarySchemaVersion": raw["version"],
            }
