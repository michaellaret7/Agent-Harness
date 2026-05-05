"""Current weather via wttr.in — no API key required."""
from __future__ import annotations

import urllib.parse

import httpx


def get_weather(location: str) -> str:
    encoded = urllib.parse.quote(location)
    # format=4 returns one line: "Seattle: ⛅ +10°C 🌬️↘13km/h"
    response = httpx.get(f'https://wttr.in/{encoded}?format=4', timeout=10.0)
    response.raise_for_status()
    return response.text.strip()


tool = {
    'name': 'get_weather',
    'description': 'Return the current weather for a city or location.',
    'parameters': {
        'type': 'object',
        'properties': {
            'location': {
                'type': 'string',
                'description': 'City name, e.g. "Seattle" or "Paris, France".',
            },
        },
        'required': ['location'],
    },
    'function': get_weather,
}
