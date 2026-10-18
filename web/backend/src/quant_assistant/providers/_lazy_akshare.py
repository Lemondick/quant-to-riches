"""Load the optional public feed runtime only when a feed function is used."""

from functools import wraps
from importlib import import_module

from quant_assistant.common.performance import measure


class _LazyModule:
    def __init__(self, module: str):
        self._module = module

    def __getattr__(self, name: str):
        value = getattr(import_module(self._module), name)
        if self._module != "akshare" or not callable(value):
            return value

        @wraps(value)
        def call(*args, **kwargs):
            with measure("external_io"):
                return value(*args, **kwargs)

        return call


ak = _LazyModule("akshare")
demjson = _LazyModule("akshare.utils.demjson")
