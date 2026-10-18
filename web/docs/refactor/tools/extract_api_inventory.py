"""P0 inventory: statically extract all HTTP routes from FastAPI controllers.

Read-only AST analysis; no product code is modified. Output: api-inventory.json
next to this file. Usage:
    python extract_api_inventory.py
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

CONTROLLERS = Path(__file__).resolve().parents[4] / "web" / "backend" / "src" / "quant_assistant" / "controllers"
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}
BASELINE_COMMIT = "29568a8a0a8aa9def7241d5d199bdf5d8a111adc"


def _literal(node: ast.AST | None):
    """Return a python literal if node is one."""
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def _annot_name(node: ast.AST | None) -> str | None:
    """Render an annotation to a readable name (handles Optional[X] = Union[X, None])."""
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_annot_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Subscript):
        base = _annot_name(node.value)
        slc = node.slice
        args = []
        if isinstance(slc, ast.Tuple):
            args = [_annot_name(e) for e in slc.elts]
        else:
            args = [_annot_name(slc)]
        return f"{base}[{', '.join(a for a in args if a)}]"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return f"{_annot_name(node.left)} | {_annot_name(node.right)}"
    if isinstance(node, ast.Constant) and node.value is None:
        return "None"
    return None


def _doc_summary(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    doc = ast.get_docstring(fn)
    if not doc:
        return None
    return " ".join(doc.split())[:160]


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict]:
    """Classify parameters: path (in path template), query (Query/defaults), body (typed model)."""
    # ast stores defaults in parallel lists, not on ast.arg itself.
    pos_args = fn.args.args
    pos_defaults = [None] * (len(pos_args) - len(fn.args.defaults)) + list(fn.args.defaults)
    pairs = list(zip(pos_args, pos_defaults)) + list(
        zip(fn.args.kwonlyargs, [d if d is not None else None for d in fn.args.kw_defaults])
    )
    out = []
    for arg, default in pairs:
        name = arg.arg
        if name in {"self", "request", "response"}:
            continue
        ann = _annot_name(arg.annotation)
        kind = "query"
        if isinstance(default, ast.Call):
            callee = default.func
            callee_name = callee.id if isinstance(callee, ast.Name) else (
                callee.attr if isinstance(callee, ast.Attribute) else ""
            )
            if callee_name == "Body":
                kind = "body"
            elif callee_name == "Query":
                kind = "query"
            elif callee_name == "Depends":
                kind = "dependency"
            elif callee_name == "Path":
                kind = "path"
        elif default is None and ann and ann[0].isupper():
            kind = "body"
        required = default is None
        out.append({"name": name, "type": ann, "in": kind, "required": required})
    return out


def extract_file(path: Path) -> dict[str, dict]:
    """Return builders: name -> {"prefix", "routes", "includes"}.

    所有含 APIRouter 定义的构造函数都登记（无论是否有 include）；
    解析阶段从根（未被他人 include 的 builder）出发合并前缀。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def walk_router(fn: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str, router_var: str, extra: dict):
        found: list[dict] = []
        for item in fn.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in item.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                    continue
                method = dec.func.attr.lower()
                if method not in HTTP_METHODS or not isinstance(dec.func.value, ast.Name):
                    continue
                if dec.func.value.id != router_var:
                    continue
                rel = _literal(dec.args[0]) if dec.args else ""
                kwargs = {kw.arg: kw.value for kw in dec.keywords if kw.arg}
                resp_model = _annot_name(kwargs.get("response_model"))
                status_code = _literal(kwargs.get("status_code"))
                tags = _literal(kwargs.get("tags"))
                found.append({
                    "file": path.name,
                    "function": item.name,
                    "method": method.upper(),
                    "path": f"{prefix}{rel}",
                    "status_code": status_code or (201 if method == "post" else 200),
                    "response_model": resp_model,
                    "tags": tags,
                    "params": _params(item),
                    "summary": _doc_summary(item),
                    **extra,
                })
        return found

    builders: dict[str, dict] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        router_var = None
        prefix = ""
        for stmt in node.body:
            if not (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call)):
                continue
            call = stmt.value
            callee = call.func
            if not (isinstance(callee, ast.Name) and callee.id == "APIRouter"):
                continue
            if not (len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)):
                continue
            router_var = stmt.targets[0].id
            kw = {k.arg: k.value for k in call.keywords if k.arg}
            prefix = _literal(kw.get("prefix")) or ""
            deps = []
            if "dependencies" in kw and isinstance(kw["dependencies"], ast.List):
                deps = [_render_dep(d) for d in kw["dependencies"].elts]
            builders[node.name] = {"prefix": prefix, "routes": [], "includes": [], "auth_dependency": deps or None}
        if router_var is None:
            continue
        entry = builders[node.name]
        entry["routes"] = walk_router(node, prefix, router_var, {"auth_dependency": entry["auth_dependency"]})
        for stmt in node.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                call = stmt.value
                if (isinstance(call.func, ast.Attribute) and call.func.attr == "include_router"
                        and isinstance(call.func.value, ast.Name) and call.func.value.id == router_var):
                    if call.args and isinstance(call.args[0], ast.Call) and isinstance(call.args[0].func, ast.Name):
                        entry["includes"].append(call.args[0].func.id)
    return builders


def resolve_includes(builders: dict[str, dict]) -> list[dict]:
    """从根 builder（未被他人 include 的）出发，合并子路由前缀（递归、防环）。"""
    included_names = {c for b in builders.values() for c in b["includes"]}
    roots = [n for n in builders if n not in included_names]
    resolved_cache: dict[str, list[dict]] = {}

    def resolve(name: str, seen: frozenset[str]) -> list[dict]:
        if name in resolved_cache:
            return resolved_cache[name]
        if name in seen:
            return []
        b = builders[name]
        out = list(b["routes"])
        for child in b["includes"]:
            if child in builders:
                for r in resolve(child, seen | {name}):
                    merged = dict(r)
                    merged["path"] = f"{b['prefix']}{r['path']}"
                    out.append(merged)
        resolved_cache[name] = out
        return out

    all_routes: list[dict] = []
    for name in roots:
        all_routes.extend(resolve(name, frozenset()))
    return all_routes


def _render_dep(d: ast.AST) -> str:
    if isinstance(d, ast.Call):
        name = d.func.id if isinstance(d.func, ast.Name) else getattr(d.func, "attr", "?")
        return name
    if isinstance(d, ast.Name):
        return d.id
    return "?"


def extract_app_level_routes(path: Path) -> list[dict]:
    """Extract @app.get / @context.app.get style routes (app-level, not APIRouter)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    routes: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            method = dec.func.attr.lower()
            if method not in HTTP_METHODS:
                continue
            recv = dec.func.value
            # app 或 xxx.app 形式的接收者
            is_app = (isinstance(recv, ast.Name) and recv.id == "app") or (
                isinstance(recv, ast.Attribute) and recv.attr == "app"
            )
            if not is_app:
                continue
            rel = _literal(dec.args[0]) if dec.args else ""
            kwargs = {kw.arg: kw.value for kw in dec.keywords if kw.arg}
            in_schema = _literal(kwargs.get("include_in_schema"))
            routes.append({
                "file": path.name,
                "function": node.name,
                "method": method.upper(),
                "path": rel,
                "status_code": 200,
                "response_model": _annot_name(kwargs.get("response_model")),
                "tags": None,
                "params": _params(node),
                "summary": _doc_summary(node),
                "auth_dependency": None,
                "app_level": True,
                "include_in_schema": in_schema if in_schema is not None else True,
            })
    return routes


def main() -> None:
    all_builders: dict[str, dict] = {}
    for py in sorted(CONTROLLERS.glob("*.py")):
        if py.name == "__init__.py":
            continue
        all_builders.update(extract_file(py))

    routes = resolve_includes(all_builders)
    # app 级路由（@app.get / @context.app.get），扫描控制器与 services 中的动态挂载
    app_level: list[dict] = []
    for py in sorted(CONTROLLERS.glob("*.py")):
        if py.name != "__init__.py":
            app_level.extend(extract_app_level_routes(py))
    services_dir = CONTROLLERS.parent / "services"
    for py in sorted(services_dir.glob("*.py")):
        app_level.extend(extract_app_level_routes(py))
    routes.extend(app_level)
    routes.sort(key=lambda r: (r["path"], r["method"]))

    inventory = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": BASELINE_COMMIT,
        "source": "static AST extraction of web/backend/src/quant_assistant/controllers/*.py",
        "notes": [
            "response_model 为装饰器显式声明值；未声明的接口由 FastAPI 按返回值推断，需以运行时 OpenAPI 复核。",
            "auth_dependency 为路由级依赖；业务应用另有全局中间件（请求ID、限流、鉴权、安全头），认证网关负责会话。",
            "params.in=body 表示 Pydantic 请求体模型或 Body() 显式声明；query 为查询参数；path 为路径参数。",
        ],
        "route_count": len(routes),
        "routes": routes,
    }

    out = Path(__file__).resolve().parent.parent / "api-inventory.json"
    out.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"routes: {len(routes)} -> {out}")
    by_method: dict[str, int] = {}
    for r in routes:
        by_method[r["method"]] = by_method.get(r["method"], 0) + 1
    print("by method:", by_method)


if __name__ == "__main__":
    main()
