from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import pytest

from canbench import BenchConfig, Testbench

# ---------------------------------------------------------------- target


def _bench_config() -> BenchConfig:
    """Select the target from the environment.

    Default: in-process simulator on a private python-can virtual bus.
    CANBENCH_INTERFACE / CANBENCH_CHANNEL: run on a real interface, e.g. socketcan + vcan0.
    CANBENCH_EXTERNAL=1: don't start a simulator; test whatever BMS is on the bus.
    """
    return BenchConfig(
        interface=os.environ.get("CANBENCH_INTERFACE", "virtual"),
        channel=os.environ.get("CANBENCH_CHANNEL") or None,
        start_simulator=os.environ.get("CANBENCH_EXTERNAL", "0") != "1",
    )


@pytest.fixture
def bench():
    with Testbench(_bench_config()) as tb:
        yield tb


# ---------------------------------------------------- requirement tracing


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--traceability",
        metavar="PATH",
        help="write a requirement -> test result matrix (JSON and Markdown) to PATH.{json,md}",
    )


_results: dict[str, list[tuple[str, str]]] = defaultdict(list)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call):
    outcome = yield
    report = outcome.get_result()
    reqs = [m.args[0] for m in item.iter_markers("req")]
    if not reqs:
        return
    if report.when == "setup":
        report.user_properties.append(("requirements", ",".join(reqs)))
    is_final = (report.when == "call") or (report.when == "setup" and not report.passed)
    if is_final:
        for req in reqs:
            _results[req].append((item.nodeid, report.outcome))


def _req_status(outcomes: list[str]) -> str:
    if any(o == "failed" for o in outcomes):
        return "FAIL"
    if all(o == "passed" for o in outcomes):
        return "PASS"
    return "PARTIAL"


def pytest_terminal_summary(terminalreporter, exitstatus, config: pytest.Config) -> None:
    if not _results:
        return
    tr = terminalreporter
    tr.section("requirement coverage")
    for req in sorted(_results):
        tests = _results[req]
        status = _req_status([o for _, o in tests])
        tr.write_line(f"{req:<10} {status:<8} {len(tests)} test(s)")

    path = config.getoption("--traceability")
    if path:
        base = Path(path)
        base.parent.mkdir(parents=True, exist_ok=True)
        data = {
            req: {"status": _req_status([o for _, o in tests]), "tests": dict(tests)}
            for req, tests in sorted(_results.items())
        }
        base.with_suffix(".json").write_text(json.dumps(data, indent=2))
        lines = ["| Requirement | Status | Tests |", "|---|---|---|"]
        for req, entry in data.items():
            tests = "<br>".join(f"`{t.split('::')[-1]}` {o}" for t, o in entry["tests"].items())
            lines.append(f"| {req} | {entry['status']} | {tests} |")
        base.with_suffix(".md").write_text("\n".join(lines) + "\n")
        tr.write_line(f"traceability matrix written to {base.with_suffix('.md')}")
