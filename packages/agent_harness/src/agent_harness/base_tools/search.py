"""Web search via the Parallel Search API (GA v1).

Calls /v1/search through the official `parallel-web` SDK (which retries
429/5xx/connection errors once with backoff) and returns ranked URLs with
extended excerpts, formatted as plain text for direct LLM consumption.
Pair with `WebExtract` when an excerpt isn't enough and the model needs
the full page.
"""
from __future__ import annotations

import os
import uuid
from typing import Annotated, Literal

from parallel import APIConnectionError, APIStatusError, APITimeoutError, Parallel
from parallel.types import AdvancedSearchSettingsParam, WebSearchResult
from parallel.types.shared_params import SourcePolicy

from agent_harness.tooling.decorator import Param, agent_tool
from agent_harness.tooling.result import ToolResult

DEFAULT_TIMEOUT = 90  # Reason: 'advanced' mode can take 15-60s end-to-end. Applies per attempt.
MAX_RETRIES = 1  # Reason: absorbs a transient 429/5xx; the model retries anything beyond that.
MAX_OUTPUT_CHARS = 16000


@agent_tool(name='WebSearch', safe_parallel=True)
def search(
    objective: Annotated[str, Param(description='Natural-language description of what information you are seeking. Provides context that focuses the ranking.')],
    search_queries: Annotated[list[str], Param(description='1-5 concise keyword queries (3-6 words each), diverse across angles. 2-3 is the sweet spot.')],
    mode: Annotated[Literal['basic', 'advanced'], Param(description='"basic" is fast (2-5s) for routine lookups; "advanced" (default) uses a deeper retrieval/compression pipeline (15-60s) for higher-quality results.')] = 'advanced',
    max_results: Annotated[int, Param(description='Upper bound on returned results. Default 5.')] = 5,
    max_chars_per_result: Annotated[int, Param(description='Max characters per excerpt block. Values below 1000 are floored to 1000 by the API. Default 1500.')] = 1500,
    include_domains: Annotated[list[str] | None, Param(description='Optional allowlist of apex domains (e.g. ["arxiv.org", "nature.com"]) or wildcard TLDs (".gov", ".edu"). Restrictive — use only when single-publisher or compliance scope is required.')] = None,
    exclude_domains: Annotated[list[str] | None, Param(description='Optional blocklist of apex domains. Combined with include_domains, total must be <= 200.')] = None,
    after_date: Annotated[str | None, Param(description='Recency filter; YYYY-MM-DD. Only results published on or after this date.')] = None,
    _client_model: str | None = None,
) -> ToolResult:
    """
    Web search via the Parallel Search API. Returns ranked URLs with extended
    page excerpts optimized for LLM consumption. Use mode="basic" for routine
    queries (2-5s) and mode="advanced" (default) for higher-quality retrieval
    prioritizing freshness and relevance (15-60s). Provide a clear
    natural-language `objective` plus 1-5 diverse keyword `search_queries`.
    Follow up with `WebExtract` on any URL whose excerpt is interesting but
    truncated.
    """
    api_key = os.environ.get('PARALLEL_API_KEY')
    if not api_key:
        return ToolResult('error: PARALLEL_API_KEY not set', status='error')

    advanced: AdvancedSearchSettingsParam = {
        'excerpt_settings': {'max_chars_per_result': max_chars_per_result},
        'max_results': max_results,
    }

    source_policy: SourcePolicy = {}

    if include_domains:
        source_policy['include_domains'] = include_domains

    if exclude_domains:
        source_policy['exclude_domains'] = exclude_domains

    if after_date:
        source_policy['after_date'] = after_date

    if source_policy:
        advanced['source_policy'] = source_policy

    try:
        with Parallel(api_key=api_key, timeout=DEFAULT_TIMEOUT, max_retries=MAX_RETRIES) as client:
            response = client.search(
                objective=objective,
                search_queries=search_queries,
                mode=mode,
                session_id=uuid.uuid4().hex,
                advanced_settings=advanced,
                # Injected by Agent via bind_tool; None when the model is unresolved (vLLM).
                client_model=_client_model,
            )

    # Reason: APITimeoutError subclasses APIConnectionError, so it must be caught first.
    except APITimeoutError:
        return ToolResult(f'error: Parallel Search timed out ({DEFAULT_TIMEOUT}s per attempt, retries exhausted)', status='error')

    except APIStatusError as e:
        return ToolResult(f'error: Parallel Search returned HTTP {e.status_code}: {e.response.text[:500]}', status='error')

    except APIConnectionError as e:
        return ToolResult(f'error: Parallel Search request failed: {type(e).__name__}: {e}', status='error')

    results = response.results

    if not results:
        warnings = [w.message for w in response.warnings or []]
        suffix = f'  warnings: {warnings}' if warnings else ''
        return ToolResult(f'[no results]{suffix}', status='ok')

    return ToolResult(_format_results(results), status='ok')


def _format_results(results: list[WebSearchResult]) -> str:
    blocks: list[str] = []

    for i, r in enumerate(results, 1):
        title = r.title or '(untitled)'
        url = r.url or '(no url)'
        publish_date = r.publish_date or 'n/a'
        excerpts = r.excerpts or []

        header = f'[{i}] {title}\n{url}  (published: {publish_date})'

        body = '\n\n'.join(excerpts) if excerpts else '(no excerpts)'

        blocks.append(f'{header}\n{body}')

    output = '\n\n---\n\n'.join(blocks)

    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + f'\n\n... [truncated; {len(output) - MAX_OUTPUT_CHARS} more chars]'

    return output
