"""The official budget values must cover what the real model was measured doing.

Data-driven on purpose: the numbers live in the archived observation file next to the
evidence, and this test reads both sides. Writing the figures into the assertion would make
it a copy of `run_budget.py` that goes green whenever both are edited together — which is the
one thing it exists to prevent.

Read `docs/engineering/agent-quality-evidence.md` (Task 6.5) before changing either side: two
of these observations are **censored** — the measurement ran into the limit it is being used
to justify — and the evidence says which and why.
"""
import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OBSERVED = ROOT / 'docs/engineering/data/evals-live-observations-2026-09-09.json'
BUDGET = ROOT / 'frappe_app/dsherp_bridge/run_budget.py'


def _tables():
    """DEFAULTS and DOMAINS read straight from the source: the Site module imports frappe,
    which the host has no reason to install."""
    tree = ast.parse(BUDGET.read_text(encoding='utf-8'))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ('DEFAULTS', 'DOMAINS'):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    return found['DEFAULTS'], found['DOMAINS']


@pytest.fixture(scope='module')
def observed():
    return json.loads(OBSERVED.read_text(encoding='utf-8'))['observed']


def _plan(domain):
    defaults, domains = _tables()
    return {**defaults, **domains[domain]}


def test_every_measured_domain_has_a_budget(observed):
    _, domains = _tables()
    assert set(observed) <= set(domains), '量到了一个没有预算的领域'


@pytest.mark.parametrize('domain', ('query', 'operation', 'configuration'))
def test_the_cumulative_budgets_cover_the_observed_maximum(domain, observed):
    """The plan's acceptance criterion for the official values, in both directions."""
    plan = _plan(domain)
    per_run = observed[domain]['per_run']
    assert plan['model_max_output_tokens_total'] >= per_run['actual_output_tokens']['max']
    assert plan['model_max_input_bytes_total'] >= per_run['model_input_bytes']['max']
    assert plan['run_total_seconds'] * 1000 >= per_run['duration_ms']['max']


@pytest.mark.parametrize('domain', ('query', 'operation', 'configuration'))
def test_the_call_budget_leaves_room_above_what_was_observed(domain, observed):
    """Strictly above, not equal: operation was measured at exactly the old ceiling of 10,
    which means the measurement was cut off by the ceiling rather than by the model."""
    plan = _plan(domain)
    assert plan['model_max_calls'] > observed[domain]['per_run']['model_calls']['max']


@pytest.mark.parametrize('domain', ('query', 'operation', 'configuration'))
def test_the_per_call_output_budget_is_above_the_ceiling_that_starved_the_answer(domain, observed):
    """The one value that is not a percentile of the observation.

    Reasoning tokens are charged against the same allowance as the answer, so the observation
    is censored: five of the seven responses that reached the old ceiling spent the *whole*
    allowance on reasoning and emitted nothing. A budget at or below the ceiling that produced
    those runs would reproduce them.
    """
    plan = _plan(domain)
    ceiling_that_failed = observed[domain]['per_call']['cap_in_force']
    assert plan['model_max_output_tokens_per_call'] > max(ceiling_that_failed)
    assert plan['model_max_output_tokens_per_call'] <= plan['model_max_output_tokens_total']


def test_the_single_call_budgets_stay_within_the_cumulative_ones():
    for domain in ('query', 'operation', 'configuration'):
        plan = _plan(domain)
        assert plan['model_max_input_bytes_per_call'] <= plan['model_max_input_bytes_total']
        assert plan['model_max_output_tokens_per_call'] <= plan['model_max_output_tokens_total']


def test_the_observation_file_says_which_batch_it_came_from():
    """A number without a provenance is a number somebody can quietly re-derive."""
    payload = json.loads(OBSERVED.read_text(encoding='utf-8'))
    assert payload['mode'] == 'live' and payload['model'] and payload['site']
    assert payload['cases'] >= 30
    assert payload['turn_end_reasons']['max-tokens'] > 0, \
        '这份观测的意义就是它记录了饿死答复的那一批；没有 max-tokens 就不是那一批'


@pytest.mark.parametrize('domain', ('query', 'operation', 'configuration'))
def test_the_call_budget_is_not_secretly_smaller_than_it_says(domain):
    """`model_max_output_tokens_total` must cover every call the plan allows.

    Reservations are charged in full and never refunded, so a total below
    `model_max_calls × model_max_output_tokens_per_call` stops the run at `total // per_call`
    calls however little the model actually writes — a call limit wearing a token limit's
    name. Reproduced 2026-09-09: a query run allowed 11 calls was stopped after 3 with
    `used 32768, allowed 24576`, having emitted 570 tokens.
    """
    plan = _plan(domain)
    assert plan['model_max_output_tokens_total'] >= \
        plan['model_max_calls'] * plan['model_max_output_tokens_per_call']
