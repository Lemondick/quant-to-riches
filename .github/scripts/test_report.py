"""Run unittest or summarize TRX without treating skipped tests as passes."""

import argparse
import json
import os
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


def publish(report, destination, title):
    report["executed"] = report["tests"] - report["skipped"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"### {title}",
        "",
        "| Total | Executed | Passed | Failed | Errors | Skipped |",
        "| --- | --- | --- | --- | --- | --- |",
        "| {tests} | {executed} | {passed} | {failed} | {errors} | {skipped} |".format(**report),
        "",
    ]
    for item in report["skip_reasons"]:
        reason = item["reason"].replace("\n", " ")
        lines.append(f"- SKIPPED `{item['test']}`: {reason}")
    text = "\n".join(lines) + "\n"
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
            stream.write(text)


def python_tests(args):
    suite = unittest.defaultTestLoader.discover(args.start, pattern=args.pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    expected = len(result.expectedFailures)
    unexpected = len(result.unexpectedSuccesses)
    report = {
        "tests": result.testsRun,
        "passed": result.testsRun
        - len(result.failures)
        - len(result.errors)
        - len(result.skipped)
        - expected
        - unexpected,
        "failed": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "expected_failures": expected,
        "unexpected_successes": unexpected,
        "skip_reasons": [{"test": str(test), "reason": why} for test, why in result.skipped],
        "successful": result.wasSuccessful() and result.testsRun > len(result.skipped),
    }
    publish(report, args.report, args.title)
    return 0 if report["successful"] else 1


def dotnet_tests(args):
    root = ET.parse(args.trx).getroot()
    namespace = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}
    counters = root.find("t:ResultSummary/t:Counters", namespace)
    if counters is None:
        raise ValueError("TRX has no result counters")

    def count(name):
        return int(counters.get(name, "0"))

    skipped = []
    for item in root.findall("t:Results/t:UnitTestResult", namespace):
        if item.get("outcome") == "NotExecuted":
            reason = item.findtext(
                "t:Output/t:ErrorInfo/t:Message", default="", namespaces=namespace
            )
            skipped.append(
                {"test": item.get("testName", "unknown"), "reason": reason or "NotExecuted"}
            )
    report = {
        "tests": count("total"),
        "passed": count("passed"),
        "failed": count("failed"),
        "errors": count("error") + count("timeout") + count("aborted"),
        "skipped": count("notExecuted"),
        "skip_reasons": skipped,
        "run_outcome": root.find("t:ResultSummary", namespace).get("outcome"),
    }
    report["successful"] = (
        report["passed"] > 0
        and report["run_outcome"] == "Completed"
        and not (report["failed"] or report["errors"])
    )
    if args.require_database and any("QUANT_TEST_PG_CONNECTION" in x["reason"] for x in skipped):
        report["successful"] = False
    publish(report, args.report, args.title)
    return 0 if report["successful"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--trx", type=Path)
    parser.add_argument("--require-database", action="store_true")
    parser.add_argument("--start")
    parser.add_argument("--pattern", default="test_*.py")
    args = parser.parse_args()
    if not args.trx and not args.start:
        parser.error("--start or --trx is required")
    return dotnet_tests(args) if args.trx else python_tests(args)


if __name__ == "__main__":
    raise SystemExit(main())
