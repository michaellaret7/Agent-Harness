"""Tools whose arguments are declared as a Pydantic model.

A tool function takes one `ToolInput` subclass. That class generates the schema
the LLM sees and validates every call, so the two cannot drift:

    class GetQuoteInput(ToolInput):
        ticker: str = Field(description='Stock ticker, e.g. "AAPL".')

    @agent_tool(name='GetQuote')
    def get_quote(args: GetQuoteInput, _client: Client | None = None) -> ToolResult:
        '''Get the latest price of one stock.'''
        ...

Underscore-prefixed parameters stay on the function: hidden from the schema,
filled by `bind_tool` or the handler.
"""
from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, get_type_hints

from pydantic import BaseModel, ConfigDict, ValidationError

from agent_harness.tooling.result import ToolResult


class ToolInput(BaseModel):
    """Base class for a tool's arguments. Unknown arguments are rejected."""

    model_config = ConfigDict(extra='forbid')


#     ================================
# --> Helper funcs
#     ================================


# Schema keys whose value is data, not a nested schema
_DATA_KEYS = ('default', 'enum', 'const', 'examples')


def _flatten_schema(node: Any, defs: dict[str, Any]) -> Any:
    """Rewrite Pydantic's JSON schema into the flat form sent to providers.

    Inlines every `$ref`, drops `title` keys, and collapses `T | None` to T's
    schema, since not every provider handles `$ref` and `anyOf`.
    """
    if isinstance(node, list):
        return [_flatten_schema(item, defs) for item in node]

    if not isinstance(node, dict):
        return node

    if '$ref' in node:
        definition = defs[node['$ref'].rsplit('/', 1)[-1]]

        # A definition's description is its class docstring, written for humans, not the LLM
        inlined = {key: value for key, value in definition.items() if key != 'description'}

        node = {**inlined, **{key: value for key, value in node.items() if key != '$ref'}}

    flat: dict[str, Any] = {}

    for key, value in node.items():
        # `additionalProperties: false` only restates that unknown arguments are rejected
        if key == 'title' or (key == 'additionalProperties' and value is False):
            continue

        # Keys under `properties` are argument names, not schema keywords
        if key == 'properties':
            flat[key] = {name: _flatten_schema(prop, defs) for name, prop in value.items()}

        elif key in _DATA_KEYS:
            flat[key] = value

        else:
            flat[key] = _flatten_schema(value, defs)

    variants = [variant for variant in flat.get('anyOf', []) if variant != {'type': 'null'}]

    if len(variants) == 1:
        del flat['anyOf']
        flat = {**variants[0], **flat}

    return flat


def _parameters_schema(input_model: type[ToolInput]) -> dict[str, Any]:
    """Return the flat JSON schema of a tool's arguments."""
    schema = input_model.model_json_schema()
    defs = schema.pop('$defs', {})

    # The tool's description is the function docstring, not the input class's
    schema.pop('description', None)

    return _flatten_schema(schema, defs)


def _format_errors(error: ValidationError) -> str:
    """Render validation errors as `field: problem; field: problem` for the model to act on."""
    problems: list[str] = []

    for problem in error.errors():
        field = '.'.join(str(part) for part in problem['loc'])

        problems.append(f'{field}: {problem["msg"]}' if field else problem['msg'])

    return '; '.join(problems)


#     ================================
# --> Tool construction
#     ================================


def find_input_param(fn: Callable) -> tuple[str, type[ToolInput]] | None:
    """Return (parameter name, model) when `fn` takes a `ToolInput`, else None.

    The model must be the only parameter the LLM can set; anything else the
    function needs is an underscore-prefixed dependency.
    """
    hints = get_type_hints(fn)

    visible = [
        name
        for name, param in inspect.signature(fn).parameters.items()
        if not name.startswith('_')
        and param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]

    models = [
        name
        for name in visible
        if isinstance(hints.get(name), type) and issubclass(hints[name], ToolInput)
    ]

    if not models:
        return None

    if len(visible) > 1:
        raise TypeError(
            f'@agent_tool {fn.__name__!r}: a ToolInput must be the only model-facing parameter, got {visible}'
        )

    return models[0], hints[models[0]]


def build_model_tool(
    fn: Callable,
    param_name: str,
    input_model: type[ToolInput],
    name: str | None,
    deferred: bool,
    safe_parallel: bool,
) -> Callable:
    """Wrap `fn` so each call is validated into `input_model`, and attach its `.tool` dict."""

    @functools.wraps(fn)
    def _wrapper(**kwargs: Any) -> ToolResult:
        # Hidden `_` params are dependencies, not model arguments; they pass through untouched
        hidden = {key: value for key, value in kwargs.items() if key.startswith('_')}
        arguments = {key: value for key, value in kwargs.items() if not key.startswith('_')}

        try:
            # this line is where the data model validates the arguments
            validated = input_model.model_validate(arguments)
        except ValidationError as e:
            return ToolResult(f'error: invalid arguments: {_format_errors(e)}', status='error')

        return fn(**{param_name: validated}, **hidden)

    _wrapper.tool = {  # type: ignore[attr-defined]
        'name': name or fn.__name__,
        'description': (inspect.getdoc(fn) or '').strip(),
        'parameters': _parameters_schema(input_model),
        'function': _wrapper,
        'deferred': deferred,
        'safe_parallel': safe_parallel,
    }

    return _wrapper
