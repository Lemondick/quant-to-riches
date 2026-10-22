"""Offline runner checks using real LangGraph tool execution, with no models."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# The upstream graph facade mirrors its pinned public prepare/invoke/finalize
# lifecycle; tool events themselves go through real LangChain/LangGraph.
UPSTREAM_FACADE = '''
from pathlib import Path
from typing import Annotated, TypedDict
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

IMPORTED_HOME = Path.home()

class State(TypedDict):
    messages: Annotated[list, add_messages]

@tool
def probe(value: str) -> str:
    """Return a deterministic local value."""
    return value

class TradingAgentsGraph:
    calls = 2
    def __init__(self, **kwargs):
        self.config = kwargs['config']
        workflow = StateGraph(State)
        workflow.add_node('tools', ToolNode([probe]))
        workflow.add_edge(START, 'tools')
        workflow.add_edge('tools', END)
        self.graph = workflow.compile()

    def prepare_graph_run(self, symbol, day, callbacks=None):
        state = {'messages': [AIMessage(content='', tool_calls=[
            {'name': 'probe', 'args': {'value': 'synthetic'}, 'id': str(i)}
            for i in range(self.calls)])]}
        args = {'config': {'max_concurrency': 1}}
        if callbacks:
            args['config']['callbacks'] = callbacks
        return state, args, None

    def propagate(self, symbol, day):
        state, args, _ = self.prepare_graph_run(symbol, day)
        result = self.graph.invoke(state, **args)
        return result, self.finalize_graph_run(symbol, day, result)

    def finalize_graph_run(self, symbol, day, state):
        state['market_report'] = str(IMPORTED_HOME) + '|' + self.config['project_dir']
        state['missing_data_complete'] = True
        state['final_trade_decision'] = 'Hold'
        return 'Hold'

    def close_graph_run(self):
        pass
'''


@unittest.skipUnless(
    importlib.util.find_spec("langgraph"), "isolated TradingAgents runtime dependencies required"
)
class RunnerRuntimeRegressions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        package = root / "tradingagents" / "graph"
        package.mkdir(parents=True)
        (package.parent / "__init__.py").write_text("", encoding="utf-8")
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "trading_graph.py").write_text(UPSTREAM_FACADE, encoding="utf-8")
        self.modules = patch.dict(sys.modules)
        self.modules.start()
        self.path = patch.object(sys, "path", [str(root), *sys.path])
        self.path.start()
        spec = importlib.util.spec_from_file_location(
            "isolated_runner", Path(__file__).resolve().parents[1] / "runner.py"
        )
        self.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runner)
        self.task = {
            "schema_version": "quant-tradingagents-v1",
            "symbol": "600519.SH",
            "trade_date": "2026-01-01",
            "endpoint": "https://synthetic.example/v1",
            "api_key": "synthetic-key",
            "quick_model": "quick",
            "deep_model": "deep",
            "execution": {
                "api": "http://127.0.0.1/internal/v1/worker",
                "secret": "x" * 40,
                "lease": {"user_id": "user", "job_id": "job", "attempt": 1, "owner_token": "owner"},
            },
        }
        self.env = patch.dict(os.environ)
        self.env.start()
        self.endpoint = patch.object(
            self.runner,
            "_pin_public_endpoint",
            return_value=(self.runner.socket.getaddrinfo, "synthetic.example", ["93.184.216.34"]),
        )
        self.endpoint.start()

    def tearDown(self):
        self.endpoint.stop()
        self.env.stop()
        self.path.stop()
        self.modules.stop()
        self.directory.cleanup()

    def test_import_time_home_is_scoped_to_job_before_upstream_import(self):
        first = self.runner.run(self.task)
        imported_home, project_dir = first["analysts"]["market"].split("|")
        self.assertTrue(Path(imported_home).is_relative_to(project_dir))
        self.assertFalse(Path(project_dir).exists(), "job state must be removed after execution")

    def test_runtime_callbacks_count_real_tool_invocations(self):
        result = self.runner.run(self.task)
        self.assertEqual(result["usage"]["tool_calls"], 2)
        self.assertEqual(result["usage"]["llm_calls"], 0)

    def test_runtime_tool_limit_rejects_the_121st_tool(self):
        import tradingagents.graph.trading_graph as upstream

        upstream.TradingAgentsGraph.calls = 121
        with self.assertRaises(self.runner.RunnerFailure) as raised:
            self.runner.run(self.task)
        self.assertEqual(raised.exception.code, "tool_call_limit")


if __name__ == "__main__":
    unittest.main()
