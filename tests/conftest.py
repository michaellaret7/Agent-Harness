"""Keep the standalone sandbox and live API scripts out of pytest collection."""

collect_ignore = [
    'test_code_execution.py',
    'test_subagent_tools.py',
    'test_code_screen.py',
]
