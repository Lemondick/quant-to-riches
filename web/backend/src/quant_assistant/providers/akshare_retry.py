"""Retry and health callbacks for public AKShare requests."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from typing import TypeVar

from quant_assistant.providers.base import ProviderUnavailable

T = TypeVar("T")


def call_with_retry(
    function: Callable[..., T],
    /,
    *,
    sleep: Callable[[float], None],
    transient_errors: tuple[type[BaseException], ...],
    source: str,
    mark_success: Callable[[str], None],
    mark_failure: Callable[[str, str], None],
    logger: logging.Logger,
    attempts: int = 3,
    delays: tuple[float, ...] = (0.5, 1.0),
    **kwargs: object,
) -> T:
    """Call one upstream endpoint with bounded retries and observable health."""

    if attempts < 1:
        raise ValueError("attempts must be positive")
    if len(delays) < attempts - 1:
        raise ValueError("delays must cover every retry")
    for attempt in range(attempts):
        try:
            # Some AKShare endpoints emit tqdm bars even in non-interactive
            # processes. Keep command output machine-readable.
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                result = function(**kwargs)
            mark_success(source)
            return result
        except transient_errors as error:
            if attempt == attempts - 1:
                mark_failure(source, type(error).__name__)
                name = getattr(function, "__name__", "provider call")
                logger.warning(
                    "external AKShare source unavailable after retries: %s (%s)",
                    name,
                    type(error).__name__,
                )
                raise ProviderUnavailable(
                    f"AKShare call failed after {attempts} attempts: {name}"
                ) from error
            sleep(delays[attempt])
        except Exception as error:
            name = getattr(function, "__name__", "provider call")
            logger.exception(
                "AKShare provider code/contract failure: %s (%s)",
                name,
                type(error).__name__,
            )
            raise
    raise AssertionError("unreachable")


__all__ = ["call_with_retry"]
