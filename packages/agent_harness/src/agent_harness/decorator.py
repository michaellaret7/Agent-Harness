"""Decorator that auto-generates a tool dict from a function signature.

Attaches a `.tool` attribute to the decorated function containing the dict
expected by `Agent.add_tool`. The schema is built from type hints, defaults,
and the function docstring; per-parameter overrides are supplied via
`typing.Annotated[T, Param(...)]`. `Agent.add_tool` accepts the decorated
function directly — it reads `.tool` off it.

Trimmed port of the ProphitAI Atlas decorator — dropped `Schema()` injection
(no current consumer) and the `additionalProperties: False` line to match
the existing hand-written schemas. Runtime validation returns an `error: ...`
string (the Coding Agent tool-error convention) rather than a structured
response.

Example:

    from typing import Annotated
    from agent_harness.decorator import agent_tool, Param

    @agent_tool(name='Bash')
    def bash(
        command: Annotated[str, Param(description='The bash command.')],
        timeout: int = 120,
    ) -> str:
        '''Execute a bash command and return combined stdout/stderr.'''
        ...

    agent.add_tool(bash)

Parameter names beginning with `_` are excluded from the generated schema —
useful for hidden values injected at execution time (user_id, session, etc.).
"""
from __future__ import annotations

import functools
import inspect
import re
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Literal, Union, get_args, get_origin, get_type_hints, is_typeddict

#     ================================
# --> Helper dataclasses
#     ================================


@dataclass(frozen=True)
class Param:
    """Per-parameter metadata supplied via `Annotated[T, Param(...)]`."""

    description: str | None = None
    min_val: float | None = None
    max_val: float | None = None
    enum: list[str] | None = None


#     ================================
# --> Helper funcs
#     ================================


_TYPE_MAP: dict[type, str] = {
    str: 'string',
    int: 'integer',
    float: 'number',
    bool: 'boolean',
    dict: 'object',
    list: 'array',
}


_PARAM_SECTION_RE = re.compile(
    r'^(Args|Arguments|Parameters|Params)\s*:?\s*$',
    re.IGNORECASE,
)


_END_SECTION_RE = re.compile(
    r'^(Returns?|Raises?|Yields?|Examples?|Notes?|See Also)\s*:?\s*$',
    re.IGNORECASE,
)


def _parse_docstring(docstring: str) -> tuple[str, dict[str, str]]:
    """Return (description, {param_name: description}) from a docstring.

    Supports Google-style (`Args:\\n    name: text`) and dash-list style
    (`Parameters:\\n- name: text`). Only the Args/Parameters block is
    consumed; Returns/Raises/Examples remain in the description so the
    LLM can see them.
    """
    lines = docstring.splitlines()
    desc_lines: list[str] = []
    param_descs: dict[str, str] = {}

    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        if _PARAM_SECTION_RE.match(stripped):
            i += 1
            current: str | None = None
            parts: list[str] = []

            while i < len(lines):
                line = lines[i]
                line_stripped = line.strip()

                if _PARAM_SECTION_RE.match(line_stripped) or _END_SECTION_RE.match(line_stripped):
                    break

                match = re.match(r'^\s*-?\s*(\w+)\s*:\s*(.*)$', line)

                if match:
                    if current is not None:
                        param_descs[current] = ' '.join(parts).strip()
                    current = match.group(1)
                    parts = [match.group(2)] if match.group(2) else []

                elif current is not None and line_stripped:
                    parts.append(line_stripped)

                i += 1

            if current is not None:
                param_descs[current] = ' '.join(parts).strip()

            continue

        desc_lines.append(lines[i])
        i += 1

    return '\n'.join(desc_lines).strip(), param_descs


def _unwrap_annotated(tp: Any) -> tuple[Any, Param | None]:
    """If `tp` is `Annotated[T, Param(...)]` return (T, Param); else (tp, None).

    Uses the `__metadata__` / `__origin__` attributes rather than
    `typing.get_origin` so the check is stable across the small CPython
    version drift that touched `get_origin(Annotated[...])` behavior.
    """
    metadata = getattr(tp, '__metadata__', None)

    if metadata is None:
        return tp, None

    base = getattr(tp, '__origin__', tp)
    param_meta = next((m for m in metadata if isinstance(m, Param)), None)

    return base, param_meta


def _object_schema(td: Any) -> dict[str, Any]:
    """Build {properties, required} for a TypedDict, recursing into field types.

    Each field's type is resolved through `_resolve_type`, so Literals become
    enums and nested TypedDicts/lists nest correctly. `Annotated[T, Param(...)]`
    field descriptions are carried through.
    """
    hints = get_type_hints(td, include_extras=True)
    required_keys = getattr(td, '__required_keys__', frozenset(hints))

    properties: dict[str, Any] = {}

    for field_name, field_tp in hints.items():
        base_type, param_meta = _unwrap_annotated(field_tp)

        json_type, extra = _resolve_type(base_type)

        prop: dict[str, Any] = {'type': json_type}
        prop.update(extra)

        if param_meta is not None and param_meta.description:
            prop['description'] = param_meta.description

        properties[field_name] = prop

    schema: dict[str, Any] = {'properties': properties}

    if required_keys:
        schema['required'] = list(required_keys)

    return schema


def _resolve_type(tp: Any) -> tuple[str, dict[str, Any]]:
    """Resolve a Python type to (json_type, extra_schema_fields).

    Handles: primitives, Optional[T], list[T], Literal['a','b'], and
    TypedDict objects (emitting nested `properties`/`required`). Falls back
    to ("string", {}) for anything unrecognized.
    """
    origin = get_origin(tp)
    args = get_args(tp)

    if origin is Literal:
        return 'string', {'enum': list(args)}

    if origin is Union:
        non_none = [a for a in args if a is not type(None)]

        if len(non_none) == 1:
            return _resolve_type(non_none[0])

    if is_typeddict(tp):
        return 'object', _object_schema(tp)

    if origin is list:
        if args:
            inner_type, inner_extra = _resolve_type(args[0])
            return 'array', {'items': {'type': inner_type, **inner_extra}}

        return 'array', {}

    json_type = _TYPE_MAP.get(tp)

    if json_type:
        return json_type, {}

    return 'string', {}


def _build_param_schema(
    name: str,
    tp: Any,
    default: Any,
    kind: inspect._ParameterKind,
    docstring_desc: str | None,
) -> dict[str, Any] | None:
    """Build the JSON-Schema fragment for one parameter. Returns None if hidden."""
    if name.startswith('_'):
        return None

    if kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
        return None

    base_type, param_meta = _unwrap_annotated(tp)

    json_type, extra = _resolve_type(base_type)

    prop: dict[str, Any] = {'type': json_type}
    prop.update(extra)

    if param_meta is not None and param_meta.description:
        prop['description'] = param_meta.description

    elif docstring_desc:
        prop['description'] = docstring_desc

    if param_meta is not None:
        if param_meta.min_val is not None:
            prop['minimum'] = param_meta.min_val

        if param_meta.max_val is not None:
            prop['maximum'] = param_meta.max_val

        if param_meta.enum is not None:
            prop['enum'] = param_meta.enum

    if default is not inspect.Parameter.empty:
        prop['default'] = default

    return prop


def _build_tool_dict(
    func: Callable,
    name: str | None,
    deferred: bool = False,
    safe_parallel: bool = False,
) -> dict[str, Any]:
    """Introspect `func` and produce the dict expected by `Agent.add_tool`."""
    tool_name = name or func.__name__
    description, docstring_params = _parse_docstring(inspect.getdoc(func) or '')

    hints = get_type_hints(func, include_extras=True)
    sig = inspect.signature(func)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        tp = hints.get(param_name, str)

        prop = _build_param_schema(
            param_name,
            tp,
            param.default,
            param.kind,
            docstring_params.get(param_name),
        )

        if prop is None:
            continue

        properties[param_name] = prop

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    parameters: dict[str, Any] = {'type': 'object', 'properties': properties}

    if required:
        parameters['required'] = required

    return {
        'name': tool_name,
        'description': description,
        'parameters': parameters,
        'function': func,
        'deferred': deferred,
        'safe_parallel': safe_parallel,
    }


#     ================================
# --> Runtime validation
#     ================================


def _collect_validators(func: Callable) -> dict[str, tuple[Param | None, list | None]]:
    """Return {param_name: (Param meta, Literal values)} for params with runtime checks."""
    hints = get_type_hints(func, include_extras=True)
    sig = inspect.signature(func)

    validators: dict[str, tuple[Param | None, list | None]] = {}

    for param_name, param in sig.parameters.items():
        if param_name.startswith('_'):
            continue

        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        tp = hints.get(param_name)

        if tp is None:
            continue

        base_type, param_meta = _unwrap_annotated(tp)

        literal_values: list | None = None

        if get_origin(base_type) is Literal:
            literal_values = list(get_args(base_type))

        has_constraints = param_meta is not None and (
            param_meta.min_val is not None
            or param_meta.max_val is not None
            or param_meta.enum is not None
        )

        if has_constraints or literal_values:
            validators[param_name] = (param_meta, literal_values)

    return validators


def _check_value(
    name: str,
    value: Any,
    meta: Param | None,
    literal_values: list | None,
) -> str | None:
    """Validate one value against its constraints. Returns an error message or None."""
    if value is None:
        return None

    if literal_values is not None and value not in literal_values:
        return f"'{name}' must be one of {literal_values}, got '{value}'"

    if meta is not None:
        if meta.min_val is not None and value < meta.min_val:
            return f"'{name}' must be >= {meta.min_val}, got {value}"

        if meta.max_val is not None and value > meta.max_val:
            return f"'{name}' must be <= {meta.max_val}, got {value}"

        if meta.enum is not None and value not in meta.enum:
            return f"'{name}' must be one of {meta.enum}, got '{value}'"

    return None


#     ================================
# --> Decorator
#     ================================


def agent_tool(
    func: Callable | None = None,
    *,
    name: str | None = None,
    deferred: bool = False,
    safe_parallel: bool = False,
) -> Any:
    """Attach a `.tool` dict to the decorated function.

    Supports both bare `@agent_tool` and parameterised `@agent_tool(name='X')`.
    Must be the outermost decorator when stacked — relies on `__annotations__`
    and `__doc__`, which `functools.wraps` preserves.

    The wrapper validates arguments at call time and returns an `error: ...`
    string on bad input (matches the Coding Agent tool-error convention).

    Args:
        name: Override the tool name (defaults to the function name).
        deferred: Marks the tool as deferred — written into the tool dict
            under the `deferred` key. `Agent.add_tool` reads this flag to
            decide whether to withhold the tool's full schema until it's
            explicitly requested via `load_tool`.
        safe_parallel: Marks the tool as safe to run concurrently with other
            safe_parallel tools in the same batch. Opt-in only — defaults to
            False. Set True for pure read-only tools (no shared-state mutation,
            no subprocess side effects). The `ToolHandler` chunks consecutive
            safe_parallel calls into a thread pool while preserving the
            model's emitted order across chunk boundaries.
    """

    def _wrap(fn: Callable) -> Callable:
        tool_dict = _build_tool_dict(fn, name, deferred, safe_parallel)
        validators = _collect_validators(fn)
        cached_sig = inspect.signature(fn)

        @functools.wraps(fn)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                bound = cached_sig.bind(*args, **kwargs)
                bound.apply_defaults()

            except TypeError as e:
                return f'error: {e}'

            for pname, (meta, literal_values) in validators.items():
                if pname not in bound.arguments:
                    continue

                err = _check_value(pname, bound.arguments[pname], meta, literal_values)

                if err:
                    return f'error: {err}'

            return fn(*args, **kwargs)

        tool_dict['function'] = _wrapper
        _wrapper.tool = tool_dict  # type: ignore[attr-defined] This is where the .tool dict gets attached to the function

        return _wrapper

    if func is not None:
        return _wrap(func)

    return _wrap


#     ================================
# --> Tool binding
#     ================================


def bind_tool(fn: Callable, **injected: Any) -> dict[str, Any]:
    """Return fn's `.tool` dict with `injected` kwargs pre-bound via partial.

    Injects Agent-owned runtime state (registries, the mutable plan list) into
    a tool whose @agent_tool function declares matching underscore-prefixed
    params. Those params are hidden from the JSON schema, so the LLM never sees
    them. Only `function` is swapped; name/description/parameters are untouched.
    """
    tool_dict = dict(fn.tool)  # type: ignore[attr-defined]
    tool_dict['function'] = partial(tool_dict['function'], **injected)

    return tool_dict
