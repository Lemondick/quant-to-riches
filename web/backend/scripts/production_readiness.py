"""Console wrapper for the installed production-readiness command."""

from quant_assistant.services.production_readiness import build_report, main

__all__ = ["build_report", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
