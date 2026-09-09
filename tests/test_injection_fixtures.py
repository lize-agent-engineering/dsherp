"""The injection carriers, checked without a Site.

Two properties the plan asks for, and neither had a test — they lived only as fail-fast checks
inside `evals/setup/injection.py`, which means they were only ever evaluated while
provisioning actually ran. A carrier corpus that has quietly gone wrong is the most expensive
kind of wrong here: every injection assertion is a negative, so a marker that stopped being
unique, or a canary that collided with another case, degrades into a green that proves
nothing. That is the same shape as the case whose carrier never reached the model at all.

The Site-side counting stays where it is (it needs a Site); what is asserted here is
everything that is decidable from the corpus itself.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.setup import injection  # noqa: E402

CASES = ROOT / 'evals/cases/dsherp-daily.localhost'


def test_planting_the_same_carriers_twice_yields_the_same_text():
    """Idempotence, as the plan words it: running the fixture again must not change what is
    on the Site. The Site half is `action: unchanged`; the half decidable here is that the
    text a second run would write is byte-identical to the first."""
    first = injection.carriers_with_text()
    second = injection.carriers_with_text()
    assert first == second
    assert all(carrier['text'] for carrier in first)


def test_every_marker_and_canary_is_unique_and_carries_its_own_id():
    markers = [carrier['marker'] for carrier in injection.carriers_with_text()]
    canaries = [carrier['canary'] for carrier in injection.carriers_with_text()]
    assert len(set(markers)) == len(markers), markers
    assert len(set(canaries)) == len(canaries), canaries
    for carrier in injection.carriers_with_text():
        assert carrier['id'] in carrier['marker'] and carrier['id'] in carrier['canary']
        # A marker that is a prefix of another would make "appears exactly once" unprovable
        # by a LIKE count, which is how the Site side checks it.
        others = [item for item in markers if item != carrier['marker']]
        assert not any(carrier['marker'] in other for other in others), carrier['marker']


def test_the_text_names_the_canary_and_asks_for_something_forbidden():
    """A carrier that does not actually try to make the model act is not an injection test."""
    for carrier in injection.carriers_with_text():
        assert carrier['canary'] in carrier['text'], carrier['id']
        assert carrier['marker'] in carrier['text'], carrier['id']
        assert re.search(r'erp_propose_\w+', carrier['text']), carrier['id']


def test_every_case_carrier_is_either_planted_on_the_site_or_carried_by_the_case():
    """The corpus and the fixture must not drift apart.

    A carrier nobody reads is dead weight; a case whose carrier is neither planted nor in its
    own page context verifies nothing at all — the whole injection group is negatives, so an
    absent carrier is silently green. One case is deliberately in the second category: the
    page-context injection travels in the prompt by construction and has nothing to plant.
    """
    fixture = {carrier['marker']: carrier['canary'] for carrier in injection.carriers_with_text()}
    read = set()
    self_carried = []
    for path in sorted(CASES.glob('inject-*.json')):
        case = json.loads(path.read_text(encoding='utf-8'))
        rule = case['expect']['injection']
        read.add(rule['marker'])
        if rule['marker'] in fixture:
            assert fixture[rule['marker']] == rule['canary'], path.name
            continue
        page = json.dumps(case.get('page_context') or {}, ensure_ascii=False)
        assert rule['marker'] in page and rule['canary'] in page, path.name
        self_carried.append(path.name)
    assert len(self_carried) == 1, self_carried
    assert not set(fixture) - read, sorted(set(fixture) - read)
