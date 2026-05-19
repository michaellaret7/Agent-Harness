"""URL content extraction via the Parallel Extract API.

Posts to https://api.parallel.ai/v1/extract. Converts public URLs (including
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

import httpx

from agent.decorator import Param, agent_tool

ENDPOINT = 'https://api.parallel.ai/v1/extract'
CLIENT_MODEL = 'claude-opus-4-7'
DEFAULT_TIMEOUT = 120  # Reason: full-page extracts of slow sites can take 60s+.
MAX_URLS = 20  # Reason: API hard limit.
MAX_OUTPUT_CHARS = 24000  # Reason: extracts are deeper than search excerpts; allow more room.
FULL_CONTENT_CHAR_CAP = 20000


@agent_tool(name='WebExtract', deferred=True)
def extract(
    urls: Annotated[list[str], Param(description='1-20 public URLs to extract. JS-heavy pages and PDFs are supported.')],
    objective: Annotated[str | None, Param(description='Natural-language description of what you are looking for on these pages. When set, the API returns excerpts focused on this objective (ignored if full_content=True).')] = None,
    full_content: Annotated[bool, Param(description='If True, returns the entire page as markdown instead of focused excerpts. Use when the objective is too broad to pre-filter, or when you need details beyond what excerpts surface. Default False.')] = False,
    max_chars_per_result: Annotated[int, Param(description='Max characters per excerpt block. Values below 1000 are floored to 1000 by the API. Default 4000.')] = 4000,
) -> str:
    """
    Fetch and extract URL content via the Parallel Extract API. Returns clean
    markdown — either focused excerpts aligned to `objective` (default) or the
    full page when `full_content=True`. Handles JavaScript-rendered pages and
    PDFs. Use this after `WebSearch` when an excerpt isn't enough to answer
    the question.
    """
    api_key = os.environ.get('PARALLEL_API_KEY')
    if not api_key:
        return 'error: PARALLEL_API_KEY not set'

    if not urls:
        return 'error: urls is empty'

    if len(urls) > MAX_URLS:
        return f'error: max {MAX_URLS} urls per request, got {len(urls)}'

    advanced: dict = {
        'excerpt_settings': {'max_chars_per_result': max_chars_per_result},
    }

    if full_content:
        advanced['full_content'] = {'max_chars_per_result': FULL_CONTENT_CHAR_CAP}

    payload: dict = {
        'urls': urls,
        'client_model': CLIENT_MODEL,
        'session_id': uuid.uuid4().hex,
        'advanced_settings': advanced,
    }

    if objective:
        payload['objective'] = objective

    headers = {
        'x-api-key': api_key,
        'Content-Type': 'application/json',
    }

    try:
        response = httpx.post(ENDPOINT, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()

    except httpx.TimeoutException:
        return f'error: Parallel Extract timed out after {DEFAULT_TIMEOUT}s'

    except httpx.HTTPStatusError as e:
        return f'error: Parallel Extract returned HTTP {e.response.status_code}: {e.response.text[:500]}'

    except httpx.RequestError as e:
        return f'error: Parallel Extract request failed: {type(e).__name__}: {e}'

    data = response.json()
    results = data.get('results') or []
    errors = data.get('errors') or []

    if not results and not errors:
        warnings = data.get('warnings') or []
        suffix = f'  warnings: {warnings}' if warnings else ''
        return f'[no results]{suffix}'

    return _format_output(results, errors, full_content)


def _format_output(results: list[dict], errors: list[dict], full_content: bool) -> str:
    blocks: list[str] = []

    for i, r in enumerate(results, 1):
        title = r.get('title') or '(untitled)'
        url = r.get('url') or '(no url)'
        publish_date = r.get('publish_date') or 'n/a'

        header = f'[{i}] {title}\n{url}  (published: {publish_date})'

        if full_content and r.get('full_content'):
            body = r['full_content']

        else:
            excerpts = r.get('excerpts') or []
            body = '\n\n'.join(excerpts) if excerpts else '(no excerpts)'

        blocks.append(f'{header}\n{body}')

    output = '\n\n---\n\n'.join(blocks)

    if errors:
        err_lines = [f'  - {e.get("url", "?")} ({e.get("error_type", "?")}, http={e.get("http_status_code", "?")})' for e in errors]
        output += '\n\nerrors:\n' + '\n'.join(err_lines)

    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + f'\n\n... [truncated; {len(output) - MAX_OUTPUT_CHARS} more chars]'

    return output
