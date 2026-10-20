"""The sandbox every fund member shares: host credentials, packages, and the code screen.

Members pass `FUND_SANDBOX` to their `AgentWorkspace`; one that needs more derives from it,
e.g. `dataclasses.replace(FUND_SANDBOX, recursive_agents=True)`.
"""
from __future__ import annotations

import os

from agent_harness import SandboxConfig

# Host credentials the sandbox kernel may read (everything else in `.env` stays on the host)
SANDBOX_CREDENTIALS = ('FMP_API_KEY',)
SANDBOX_PACKAGES = (
    # Data frames and storage
    'pandas', 'numpy', 'pyarrow', 'openpyxl',
    # Market data and filings
    'fmp-data', 'edgartools',
    # Statistics, models, and charts
    'scipy', 'statsmodels', 'scikit-learn', 'matplotlib',
    # Portfolio performance and optimization
    'quantstats', 'pyportfolioopt',
)

# Fails fast with a KeyError at import if any sandbox credential is missing: load `.env` first
FUND_SANDBOX = SandboxConfig(
    env={name: os.environ[name] for name in SANDBOX_CREDENTIALS},
    packages=SANDBOX_PACKAGES,
    j_screen=True,
)
