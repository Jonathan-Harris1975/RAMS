#!/usr/bin/env python3
"""Repeatable, secret-free RAMS eco-micro diagnostic benchmark.

Run against the current checkout:
    python scripts/emicro_benchmark.py --label optimised

For a comparison checkout, put that checkout first on PYTHONPATH before running
this script. The benchmark creates only disposable fixtures and never contacts
R2, GitHub or OpenRouter.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import os
import resource
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if importlib.util.find_spec("repo_mgmt") is None and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _configure_fake_environment(repo: Path) -> None:
    values = {
        "R2_ENDPOINT": "https://example.invalid",
        "R2_ACCESS_KEY_ID": "benchmark",
        "R2_SECRET_ACCESS_KEY": "benchmark",
        "R2_BUCKET_AUDITS": "audits",
        "OPENROUTER_API_KEY": "benchmark",
        "OPENROUTER_PRIMARY_MODEL": "benchmark/primary",
        "OPENROUTER_SECONDARY_MODEL": "benchmark/secondary",
        "OPENROUTER_TRIAGE_MODEL": "benchmark/triage",
        "RMS_WEBSITE_REPO_PATH": str(repo),
        "RMS_AIMS_REPO_PATH": str(repo),
        "RMS_DRY_RUN": "true",
        "RMS_LIVE_WRITE_ENABLED": "false",
        "RMS_API_KEY": "benchmark-key",
        "RMS_ALLOW_UNAUTHENTICATED_DEV": "false",
    }
    for key, value in values.items():
        os.environ.setdefault(key, value)


def _timed_peak(call: Callable[[], Any]) -> tuple[Any, float, int]:
    tracemalloc.start()
    started = time.perf_counter()
    result = call()
    elapsed_ms = (time.perf_counter() - started) * 1000
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, elapsed_ms, peak


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="current")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="rams-emicro-benchmark-") as tmp:
        fixture = Path(tmp)
        (fixture / "src").mkdir()
        (fixture / "node_modules" / "package").mkdir(parents=True)
        (fixture / "dist").mkdir()
        for index in range(200):
            (fixture / "src" / f"file-{index:03d}.txt").write_text(
                "x" * 1024, encoding="utf-8"
            )
        for index in range(400):
            (fixture / "node_modules" / "package" / f"dep-{index:03d}.js").write_text(
                "x" * 1024, encoding="utf-8"
            )
        for index in range(100):
            (fixture / "dist" / f"bundle-{index:03d}.js").write_text(
                "x" * 1024, encoding="utf-8"
            )
        context_paths: list[str] = []
        for index in range(60):
            path = fixture / "src" / f"context-{index:03d}.txt"
            path.write_text("c" * 16_384, encoding="utf-8")
            context_paths.append(path.relative_to(fixture).as_posix())

        _configure_fake_environment(fixture)
        import_started = time.perf_counter()
        from fastapi.testclient import TestClient
        from repo_mgmt import api, context_builder, repo_index, validation_runner

        import_ms = (time.perf_counter() - import_started) * 1000

        index_parameters = inspect.signature(repo_index.build_static_index).parameters
        index_kwargs: dict[str, Any] = {}
        if "max_files" in index_parameters:
            index_kwargs["max_files"] = 20_000
        index, index_ms, index_peak = _timed_peak(
            lambda: repo_index.build_static_index(fixture, **index_kwargs)
        )

        context_parameters = inspect.signature(context_builder.load_context).parameters
        context_kwargs: dict[str, Any] = {}
        if "max_files" in context_parameters:
            context_kwargs.update(
                max_files=8,
                max_file_bytes=131_072,
                max_total_bytes=524_288,
            )
        context, context_ms, context_peak = _timed_peak(
            lambda: context_builder.load_context(
                context_paths, fixture, **context_kwargs
            )
        )
        context_bytes = sum(len(value.encode("utf-8")) for value in context.values())

        validation_parameters = inspect.signature(
            validation_runner.run_commands
        ).parameters
        validation_kwargs: dict[str, Any] = {"timeout_seconds": 10}
        if "max_output_lines" in validation_parameters:
            validation_kwargs.update(max_output_lines=20, max_output_bytes=16_384)
        validation, validation_ms, validation_peak = _timed_peak(
            lambda: validation_runner.run_commands(
                ["python -c \"[print('v' * 1000) for _ in range(500)]\""],
                cwd=fixture,
                **validation_kwargs,
            )
        )

        health_times: list[float] = []
        with TestClient(api.app) as client:
            for _ in range(100):
                started = time.perf_counter()
                response = client.get("/health")
                response.raise_for_status()
                health_times.append((time.perf_counter() - started) * 1000)

        max_rss_kb = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        result = {
            "label": args.label,
            "python": os.sys.version.split()[0],
            "moduleRoot": str(Path(repo_index.__file__).resolve().parents[1]),
            "importMs": round(import_ms, 3),
            "maxRssKb": max_rss_kb,
            "health": {
                "requests": len(health_times),
                "meanMs": round(statistics.fmean(health_times), 3),
                "p50Ms": round(_percentile(health_times, 0.50), 3),
                "p95Ms": round(_percentile(health_times, 0.95), 3),
            },
            "repositoryIndex": {
                "durationMs": round(index_ms, 3),
                "tracemallocPeakBytes": index_peak,
                "indexedFiles": len(index["file_list"]),
                "truncated": bool(index.get("truncated", False)),
                "fixtureFiles": 760,
            },
            "context": {
                "durationMs": round(context_ms, 3),
                "tracemallocPeakBytes": context_peak,
                "filesLoaded": len(context),
                "bytesLoaded": context_bytes,
                "fixtureFiles": len(context_paths),
            },
            "validation": {
                "durationMs": round(validation_ms, 3),
                "tracemallocPeakBytes": validation_peak,
                "passed": validation.passed,
                "retainedOutputBytes": len(validation.output_tail.encode("utf-8")),
            },
            "externalCalls": 0,
        }
        rendered = json.dumps(result, indent=2, sort_keys=True)
        print(rendered)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
