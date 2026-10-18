"""Fail a verification run when repository text contains likely secrets."""

from quant_assistant.security_scan import scan_tracked_files_for_secrets

__all__ = ["scan_tracked_files_for_secrets"]


def main() -> int:
    findings = scan_tracked_files_for_secrets()
    if findings:
        print("Potential secrets detected:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("No secrets detected.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
