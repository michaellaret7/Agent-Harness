"""Web search via the Parallel Search API (GA v1).

Posts to https://api.parallel.ai/v1/search and returns ranked URLs with
extended excerpts, formatted as plain text for direct LLM consumption.
Pair with `WebExtract` when an excerpt isn't enough and the model needs
the full page.
"""
from __future__ import annotations

import os
import uuid
from typing import Annotated, Literal

import httpx

from agent.decorator import Param, agent_tool

ENDPOINT = 'https://api.parallel.ai/v1/search'
CLIENT_MODEL = 'claude-opus-4-7'
DEFAULT_TIMEOUT = 90  # Reason: 'advanced' mode can take 15-60s end-to-end.
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
) -> str:
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
        return 'error: PARALLEL_API_KEY not set'

    advanced: dict = {
        'excerpt_settings': {'max_chars_per_result': max_chars_per_result},
        'max_results': max_results,
    }

    source_policy: dict = {}

    if include_domains:
        source_policy['include_domains'] = include_domains

    if exclude_domains:
        source_policy['exclude_domains'] = exclude_domains

    if after_date:
        source_policy['after_date'] = after_date

    if source_policy:
        advanced['source_policy'] = source_policy

    payload = {
        'objective': objective,
        'search_queries': search_queries,
        'mode': mode,
        'client_model': CLIENT_MODEL,
        'session_id': uuid.uuid4().hex,
        'advanced_settings': advanced,
    }
    headers = {
        'x-api-key': api_key,
        'Content-Type': 'application/json',
    }

    try:
        response = httpx.post(ENDPOINT, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()

    except httpx.TimeoutException:
        return f'error: Parallel Search timed out after {DEFAULT_TIMEOUT}s'

    except httpx.HTTPStatusError as e:
        return f'error: Parallel Search returned HTTP {e.response.status_code}: {e.response.text[:500]}'

    except httpx.RequestError as e:
        return f'error: Parallel Search request failed: {type(e).__name__}: {e}'

    data = response.json()
    results = data.get('results') or []

    if not results:
        warnings = data.get('warnings') or []
        suffix = f'  warnings: {warnings}' if warnings else ''
        return f'[no results]{suffix}'

    return _format_results(results)


def _format_results(results: list[dict]) -> str:
    blocks: list[str] = []

    for i, r in enumerate(results, 1):
        title = r.get('title') or '(untitled)'
        url = r.get('url') or '(no url)'
        publish_date = r.get('publish_date') or 'n/a'
        excerpts = r.get('excerpts') or []

        header = f'[{i}] {title}\n{url}  (published: {publish_date})'

        body = '\n\n'.join(excerpts) if excerpts else '(no excerpts)'

        blocks.append(f'{header}\n{body}')

    output = '\n\n---\n\n'.join(blocks)

    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + f'\n\n... [truncated; {len(output) - MAX_OUTPUT_CHARS} more chars]'

    return output
