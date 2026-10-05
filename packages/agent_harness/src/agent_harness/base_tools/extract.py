"""URL content extraction via the Parallel Extract API.

Calls /v1/extract through the official `parallel-web` SDK (which retries
429/5xx/connection errors once with backoff). Converts public URLs (including
JavaScript-rendered pages and PDFs) into clean markdown. The natural follow-up
to WebSearch: when an excerpt is promising but truncated, hand the URL to
WebExtract to read the full article.

Two modes:
- focused (full_content=False, default) — API uses `objective` to pull only
  relevant chunks; lower cost, higher signal-to-noise.
- full (full_content=True) — entire page as markdown; use when chasing
  details or the objective is too broad to pre-filter.
"""
from __future__ import annotations

import os
import uuid
from typing import Annotated

from parallel import APIConnectionError, APIStatusError, APITimeoutError, Parallel
from parallel.types import AdvancedExtractSettingsParam, ExtractError, ExtractResult

from agent_harness.tooling.decorator import Param, agent_tool
from agent_harness.tooling.result import ToolResult

DEFAULT_TIMEOUT = 120  # Reason: full-page extracts of slow sites can take 60s+. Applies per attempt.
MAX_RETRIES = 1  # Reason: absorbs a transient 429/5xx; the model retries anything beyond that.
MAX_URLS = 20  # Reason: API hard limit.
MAX_OUTPUT_CHARS = 24000  # Reason: extracts are deeper than search excerpts; allow more room.
FULL_CONTENT_CHAR_CAP = 20000


@agent_tool(name='WebExtract', safe_parallel=True)
def extract(
    urls: Annotated[list[str], Param(description='1-20 public URLs to extract. JS-heavy pages and PDFs are supported.')],
    objective: Annotated[str | None, Param(description='Natural-language description of what you are looking for on these pages. When set, the API returns excerpts focused on this objective (ignored if full_content=True).')] = None,
    full_content: Annotated[bool, Param(description='If True, returns the entire page as markdown instead of focused excerpts. Use when the objective is too broad to pre-filter, or when you need details beyond what excerpts surface. Default False.')] = False,
    max_chars_per_result: Annotated[int, Param(description='Max characters per excerpt block. Values below 1000 are floored to 1000 by the API. Default 4000.')] = 4000,
    _client_model: str | None = None,
) -> ToolResult:
    """
    Fetch and extract URL content via the Parallel Extract API. Returns clean
    markdown — either focused excerpts aligned to `objective` (default) or the
    full page when `full_content=True`. Handles JavaScript-rendered pages and
    PDFs. Use this after `WebSearch` when an excerpt isn't enough to answer
    the question.
    """
    api_key = os.environ.get('PARALLEL_API_KEY')
    if not api_key:
        return ToolResult('error: PARALLEL_API_KEY not set', status='error')

    if not urls:
        return ToolResult('error: urls is empty', status='error')

    if len(urls) > MAX_URLS:
        return ToolResult(f'error: max {MAX_URLS} urls per request, got {len(urls)}', status='error')

    advanced: AdvancedExtractSettingsParam = {
        'excerpt_settings': {'max_chars_per_result': max_chars_per_result},
    }

    if full_content:
        advanced['full_content'] = {'max_chars_per_result': FULL_CONTENT_CHAR_CAP}

    try:
        with Parallel(api_key=api_key, timeout=DEFAULT_TIMEOUT, max_retries=MAX_RETRIES) as client:
            response = client.extract(
                urls=urls,
                objective=objective,
                session_id=uuid.uuid4().hex,
                advanced_settings=advanced,
                # Injected by Agent via bind_tool; None when the model is unresolved (vLLM).
                client_model=_client_model,
            )

    # Reason: APITimeoutError subclasses APIConnectionError, so it must be caught first.
    except APITimeoutError:
        return ToolResult(f'error: Parallel Extract timed out ({DEFAULT_TIMEOUT}s per attempt, retries exhausted)', status='error')

    except APIStatusError as e:
        return ToolResult(f'error: Parallel Extract returned HTTP {e.status_code}: {e.response.text[:500]}', status='error')

    except APIConnectionError as e:
        return ToolResult(f'error: Parallel Extract request failed: {type(e).__name__}: {e}', status='error')

    results = response.results
    errors = response.errors

    if not results and not errors:
        warnings = [w.message for w in response.warnings or []]
        suffix = f'  warnings: {warnings}' if warnings else ''
        return ToolResult(f'[no results]{suffix}', status='ok')

    return ToolResult(
        _format_output(results, errors, full_content),
        status='error' if errors else 'ok',
    )


def _format_output(results: list[ExtractResult], errors: list[ExtractError], full_content: bool) -> str:
    blocks: list[str] = []

    for i, r in enumerate(results, 1):
        title = r.title or '(untitled)'
        url = r.url or '(no url)'
        publish_date = r.publish_date or 'n/a'

        header = f'[{i}] {title}\n{url}  (published: {publish_date})'

        if full_content and r.full_content:
            body = r.full_content

        else:
            excerpts = r.excerpts or []
            body = '\n\n'.join(excerpts) if excerpts else '(no excerpts)'

        blocks.append(f'{header}\n{body}')

    output = '\n\n---\n\n'.join(blocks)

    if errors:
        err_lines = [f'  - {e.url} ({e.error_type}, http={e.http_status_code})' for e in errors]
        output += '\n\nerrors:\n' + '\n'.join(err_lines)

    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + f'\n\n... [truncated; {len(output) - MAX_OUTPUT_CHARS} more chars]'

    return output
