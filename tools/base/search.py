"""Web search via the Parallel Search API.

Posts to https://api.parallel.ai/v1/search and returns ranked URLs with
extended excerpts, formatted as plain text for direct LLM consumption.
"""
from __future__ import annotations

import os
from typing import Annotated, Literal

import httpx

from agent.decorator import Param, agent_tool

ENDPOINT = 'https://api.parallel.ai/v1beta/search'  # Reason: /v1/search rejects processor/max_results/max_chars_per_result; /v1beta accepts them.
DEFAULT_TIMEOUT = 90  # Reason: 'pro' processor can take 15-60s.
MAX_OUTPUT_CHARS = 16000


@agent_tool(name='WebSearch')
def search(
    objective: Annotated[str, Param(description='Natural-language description of what information you are seeking.')],
    search_queries: Annotated[list[str], Param(description='Keyword queries to dispatch in parallel.')],
    processor: Annotated[Literal['base', 'pro'], Param(description='Search tier. "base" is fast/cheap (2-5s); "pro" is higher quality (15-60s). Default "base".')] = 'base',
    max_results: Annotated[int, Param(description='Maximum results to return. Default 5.')] = 5,
    max_chars_per_result: Annotated[int, Param(description='Max characters per excerpt block. Min 100; values over 30000 are not guaranteed. Default 1500.')] = 1500,
) -> str:
    """
    Web search via the Parallel Search API. Returns ranked URLs with extended
    page excerpts optimized for LLM consumption. Use processor="base" (default,
    2-5s) for routine queries; processor="pro" (15-60s) for higher-quality
    retrieval prioritizing freshness and relevance. Provide a clear
    natural-language `objective` describing what you are looking for, plus 1-N
    keyword `search_queries` to dispatch.
    """
    api_key = os.environ.get('PARALLEL_API_KEY')
    if not api_key:
        return 'error: PARALLEL_API_KEY not set'

    payload = {
        'objective': objective,
        'search_queries': search_queries,
        'processor': processor,
        'max_results': max_results,
        'max_chars_per_result': max_chars_per_result,
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
