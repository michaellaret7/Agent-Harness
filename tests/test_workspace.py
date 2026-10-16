"""AgentWorkspace contracts. Run: uv run --all-packages pytest tests/test_workspace.py"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_harness import AgentWorkspace
from agent_harness.base_tools.code_execution.tool import execute_code_tool
from agent_harness.workspace.workspace import workspaces_dir
from contract_support import message_text, scripted_agent, tool_call, tool_delta

# Code the model runs: one well-formed skill and one with no frontmatter
WRITE_SKILLS = '''
from pathlib import Path

good = Path("skills/learned/dcf-valuation")
good.mkdir(parents=True, exist_ok=True)
(good / "SKILL.md").write_text("---\\nname: dcf-valuation\\ndescription: Value a company with a 5-year DCF.\\n---\\n\\n1. Pull cash flows.\\n")

bad = Path("skills/learned/notes")
bad.mkdir(parents=True, exist_ok=True)
(bad / "SKILL.md").write_text("just some notes, no frontmatter")

print("written")
'''

# Code the model runs: a layered skill whose SKILL.md links a reference and a script
WRITE_LAYERED_SKILL = '''
from pathlib import Path

skill = Path("skills/learned/fcf-yield")
(skill / "references").mkdir(parents=True, exist_ok=True)
(skill / "scripts").mkdir(parents=True, exist_ok=True)

(skill / "SKILL.md").write_text(
    "---\\nname: fcf-yield\\ndescription: Rank tickers by free-cash-flow yield; use when screening for value.\\n---\\n\\n"
    "## Steps\\n1. Run `scripts/fcf.py` (`fcf_yield(fcf, market_cap) -> float`).\\n\\n"
    "## Files\\n- `references/caveats.md`: when FCF yield misleads.\\n- `scripts/fcf.py`: run it, do not rewrite it.\\n"
)
(skill / "references" / "caveats.md").write_text("Cyclical capex distorts a single year.\\n")
(skill / "scripts" / "fcf.py").write_text(
    "def fcf_yield(fcf: float, market_cap: float) -> float:\\n"
    "    \\"\\"\\"Free cash flow divided by market cap.\\"\\"\\"\\n"
    "    return fcf / market_cap\\n"
)
print("written")
'''

# Code the model runs: the traversal the protocol teaches, a script run with runpy instead of read
RUN_SKILL_SCRIPT = '''
import runpy

fns = runpy.run_path("skills/learned/fcf-yield/scripts/fcf.py")
print(fns["fcf_yield"](5.0, 100.0))
'''


#     ================================
# --> Helper funcs
#     ================================


def write_skill(folder: Path, name: str, description: str) -> None:
    """Write a well-formed SKILL.md into `folder/<name>/`."""
    (folder / name).mkdir(parents=True)
    (folder / name / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: {description}\n---\n\nSteps.\n')


#     ================================
# --> Contract tests
#     ================================


@pytest.fixture(autouse=True)
def harness_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every workspace in this test's temp folder instead of the real home directory."""
    monkeypatch.setenv('AGENT_HARNESS_HOME', str(tmp_path))


def test_agent_writes_skills_with_code_and_the_next_session_sees_them_indexed(tmp_path: Path) -> None:
    """Skills written through ExecuteCode land in the workspace and are indexed in the next session's system prompt."""
    responses = [[tool_delta(tool_call('ExecuteCode', {'code': WRITE_SKILLS}, 'write'))], [{'content': 'done'}]]

    with scripted_agent(responses) as (agent, api, sink):
        AgentWorkspace('analyst').attach(agent)

        assert agent.run('Save a DCF skill.', sink=sink) == 'done'

    # The protocol is in the system prompt; no skills existed at attach, so there is no index
    assert '<skills_protocol>' in message_text(api.requests[0]['messages'][0])
    assert '<skills>\n' not in message_text(api.requests[1]['messages'][0])

    # The next session's system prompt lists the valid skill and flags the malformed one
    with scripted_agent([[{'content': 'ok'}]]) as (agent, api, sink):
        AgentWorkspace('analyst').attach(agent)
        agent.run('Anything.', sink=sink)

    system = message_text(api.requests[0]['messages'][0])
    assert '- dcf-valuation [learned]: Value a company with a 5-year DCF.' in system
    assert '- notes [learned]: INVALID (missing --- frontmatter block), rewrite it' in system

    # The files are real and live under the workspace's sandbox
    assert (tmp_path / 'workspaces' / 'analyst' / 'sandbox' / 'skills' / 'learned' / 'dcf-valuation' / 'SKILL.md').exists()


def test_layered_skill_is_indexed_and_its_script_runs_through_runpy() -> None:
    """A skill folder with references/ and scripts/ linked from SKILL.md is valid, and its script runs as the protocol says."""
    responses = [
        [tool_delta(tool_call('ExecuteCode', {'code': WRITE_LAYERED_SKILL}, 'write'))],
        [tool_delta(tool_call('ExecuteCode', {'code': RUN_SKILL_SCRIPT}, 'run'))],
        [{'content': 'done'}],
    ]

    with scripted_agent(responses) as (agent, api, sink):
        AgentWorkspace('analyst').attach(agent)

        assert agent.run('Save and use an FCF yield skill.', sink=sink) == 'done'

    # The protocol teaches the layered layout and how to run a script
    system = message_text(api.requests[0]['messages'][0])
    assert 'references/' in system and 'runpy.run_path' in system

    # The script ran from the sandbox root, at the path the protocol gives
    assert '0.05' in message_text(api.requests[2]['messages'][-1])

    assert AgentWorkspace('analyst').skills() == (
        '<skills>\n- fcf-yield [learned]: Rank tickers by free-cash-flow yield; use when screening for value.\n</skills>'
    )


def test_bundled_file_not_linked_from_skill_md_is_flagged() -> None:
    """A reference SKILL.md never mentions cannot be found, so the index flags it; caches are ignored."""
    workspace = AgentWorkspace('analyst')
    write_skill(workspace.skills_dir / 'learned', 'dcf-valuation', 'DCF.')
    skill = workspace.skills_dir / 'learned' / 'dcf-valuation'
    (skill / 'references').mkdir()
    (skill / 'references' / 'wacc.md').write_text('WACC inputs.')
    (skill / 'scripts' / '__pycache__').mkdir(parents=True)
    (skill / 'scripts' / '__pycache__' / 'dcf.cpython-312.pyc').write_bytes(b'')

    assert (
        '- dcf-valuation [learned]: INVALID (SKILL.md does not link references/wacc.md; link each file or delete it), rewrite it'
        in (workspace.skills() or '')
    )


def test_skills_persist_into_a_new_workspace_on_the_same_root(tmp_path: Path) -> None:
    """A workspace reopened on the same folder sees the skills an earlier one left behind."""
    write_skill(tmp_path / 'workspaces' / 'analyst' / 'sandbox' / 'skills' / 'learned', 'earnings-screen', 'Screen for earnings surprises.')

    reopened = AgentWorkspace('analyst')

    assert reopened.skills() == '<skills>\n- earnings-screen [learned]: Screen for earnings surprises.\n</skills>'


def test_name_must_match_folder() -> None:
    """A skill whose frontmatter name differs from its folder is flagged, not trusted."""
    workspace = AgentWorkspace('analyst')
    skill = workspace.skills_dir / 'learned' / 'dcf'
    skill.mkdir()
    (skill / 'SKILL.md').write_text('---\nname: dcf-valuation\ndescription: DCF.\n---\n')

    assert 'dcf [learned]: INVALID (name must be a lowercase slug matching the folder), rewrite it' in (workspace.skills() or '')


def test_curated_skills_are_synced_tagged_and_listed_first(tmp_path: Path) -> None:
    """Curated skills are copied in on attach and listed before learned ones, each tagged with its tier."""
    source = tmp_path / 'repo_skills'
    write_skill(source, 'dcf-valuation', 'Five-year FCFF DCF.')

    workspace = AgentWorkspace('analyst', curated_skills=source)
    write_skill(workspace.skills_dir / 'learned', 'capex-adjustment', 'Normalise FCF for a capex cycle.')

    with scripted_agent([]) as (agent, _, _sink):
        workspace.attach(agent)

    assert workspace.skills() == (
        '<skills>\n'
        '- dcf-valuation [curated]: Five-year FCFF DCF.\n'
        '- capex-adjustment [learned]: Normalise FCF for a capex cycle.\n'
        '</skills>'
    )


def test_agent_edits_to_curated_skills_are_discarded_on_next_attach(tmp_path: Path) -> None:
    """Curated skills are read-only in effect: whatever the agent changed is replaced from the source."""
    source = tmp_path / 'repo_skills'
    write_skill(source, 'dcf-valuation', 'Five-year FCFF DCF.')
    first = AgentWorkspace('analyst', curated_skills=source)

    with scripted_agent([]) as (agent, _, _sink):
        first.attach(agent)

    curated = first.skills_dir / 'curated' / 'dcf-valuation' / 'SKILL.md'
    curated.write_text('---\nname: dcf-valuation\ndescription: Agent rewrote this.\n---\n')
    (first.skills_dir / 'curated' / 'stray').mkdir()

    with scripted_agent([]) as (agent, _, _sink):
        AgentWorkspace('analyst', curated_skills=source).attach(agent)

    assert 'Five-year FCFF DCF.' in curated.read_text()
    assert not (first.skills_dir / 'curated' / 'stray').exists()


def test_curated_skill_with_bad_format_is_reported_not_rewritten(tmp_path: Path) -> None:
    """The agent is told not to fix a broken curated skill itself: a human fixes it at the source."""
    source = tmp_path / 'repo_skills'
    (source / 'broken').mkdir(parents=True)
    (source / 'broken' / 'SKILL.md').write_text('no frontmatter')
    workspace = AgentWorkspace('analyst', curated_skills=source)

    with scripted_agent([]) as (agent, _, _sink):
        workspace.attach(agent)

    assert '- broken [curated]: INVALID (missing --- frontmatter block), do not edit it, note it in a learned skill' in (workspace.skills() or '')


def test_missing_curated_source_fails_at_construction(tmp_path: Path) -> None:
    """A typo in the curated path fails immediately instead of silently giving an agent no skills."""
    with pytest.raises(ValueError, match='curated_skills folder not found'):
        AgentWorkspace('analyst', curated_skills=tmp_path / 'nope')


def test_attach_refuses_a_second_sandbox() -> None:
    """An agent that already has ExecuteCode would silently keep its own sandbox, so attach fails fast."""
    with scripted_agent([], [execute_code_tool()]) as (agent, _, _sink):
        with pytest.raises(ValueError, match='already has an ExecuteCode tool'):
            AgentWorkspace('analyst').attach(agent)


def test_attach_after_first_run_fails_without_registering_anything() -> None:
    """The system prompt is fixed once the agent has run, so attach raises before adding any tool."""
    with scripted_agent([[{'content': 'hi'}]]) as (agent, _, sink):
        agent.run('Hi.', sink=sink)

        with pytest.raises(RuntimeError, match='conversation already started'):
            AgentWorkspace('analyst').attach(agent)

        assert 'ExecuteCode' not in agent.tool_functions


def test_workspace_lives_under_the_harness_home_not_the_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A workspace is addressed by name and resolves under ~/.agent_harness (or AGENT_HARNESS_HOME)."""
    assert AgentWorkspace('analyst').root == tmp_path / 'workspaces' / 'analyst'

    monkeypatch.delenv('AGENT_HARNESS_HOME')
    assert workspaces_dir() == Path.home() / '.agent_harness' / 'workspaces'


@pytest.mark.parametrize('name', ['../escape', 'C:/repo/workspaces', 'Analyst', ''])
def test_workspace_name_must_be_a_slug(name: str) -> None:
    """A path or odd name cannot place a workspace outside the harness home."""
    with pytest.raises(ValueError, match='lowercase slug'):
        AgentWorkspace(name)
