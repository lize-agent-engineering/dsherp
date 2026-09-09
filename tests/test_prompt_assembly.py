"""What the model saw this run, and whether we can say so afterwards.

A run whose assembly cannot be named is not reproducible, so the summary is read from the
sha256-pinned manifest and the SKILL.md frontmatter — and a failure to read it raises rather
than degrading to None. Exact reproduction is `runtime_revision`'s job; the version number
here is for a person reading the audit.
"""
import json
import re
import shutil

import pytest

from dsherp import prompt_assembly


def _bundle(tmp_path):
    root = tmp_path / 'root'
    (root / 'config').mkdir(parents=True)
    shutil.copy(prompt_assembly.ROOT / 'config/business-skills.json', root / 'config/business-skills.json')
    for domain in ('query', 'operation', 'configuration'):
        target = root / 'business-skills' / f'erp-{domain}'
        target.mkdir(parents=True)
        shutil.copy(prompt_assembly.ROOT / 'business-skills' / f'erp-{domain}' / 'SKILL.md', target / 'SKILL.md')
    return root


def test_prompt_version_is_declared():
    """A number, not a hash: it is for a person reading the audit. Exact reproduction is
    runtime_revision's job, and this file is inside that fingerprint."""
    assert prompt_assembly.PROMPT_VERSION.isdigit()


def test_skill_summary_matches_the_manifest_version_for_each_domain():
    assert prompt_assembly.skill_summary('query') == {
        'name': 'erp-query', 'version': '1.4.0',
        'description': '在当前业务用户权限与服务端策略允许的业务对象中执行只读查询，并给出有来源的业务回答。'}
    for domain, name, version in (('operation', 'erp-operation', '2.4.0'),
                                  ('configuration', 'erp-configuration', '1.1.0')):
        summary = prompt_assembly.skill_summary(domain)
        assert summary['name'] == name and summary['version'] == version
        assert summary['description'] and not summary['description'].startswith('#')


def test_skill_summary_fails_fast_when_the_domain_is_unknown():
    with pytest.raises(ValueError, match='erp-nosuch'):
        prompt_assembly.skill_summary('nosuch')


def test_skill_summary_fails_fast_when_the_description_is_empty(tmp_path):
    root = _bundle(tmp_path)
    path = root / 'business-skills/erp-query/SKILL.md'
    blanked = re.sub(r'(?m)^description:.*$', 'description:', path.read_text(encoding='utf-8'))
    assert '\ndescription:\n' in blanked
    path.write_text(blanked, encoding='utf-8')
    with pytest.raises(ValueError, match='erp-query'):
        prompt_assembly.skill_summary('query', root=root)


def test_skill_summary_fails_fast_when_the_body_version_drifts_from_the_manifest(tmp_path):
    root = _bundle(tmp_path)
    body = (root / 'business-skills/erp-query/SKILL.md').read_text(encoding='utf-8')
    (root / 'business-skills/erp-query/SKILL.md').write_text(
        body.replace('version: 1.4.0', 'version: 9.9.9'), encoding='utf-8')
    with pytest.raises(ValueError, match='erp-query'):
        prompt_assembly.skill_summary('query', root=root)


def test_skill_summary_fails_fast_when_the_directory_is_missing(tmp_path):
    root = _bundle(tmp_path)
    shutil.rmtree(root / 'business-skills/erp-query')
    with pytest.raises(ValueError, match='erp-query'):
        prompt_assembly.skill_summary('query', root=root)


def test_sampling_note_records_that_nothing_is_set():
    """dsherp never sends a sampling parameter; the fact recorded is that fact, not a number."""
    assert prompt_assembly.sampling_note() == 'provider-default'


def test_prompt_assembly_is_part_of_the_runtime_fingerprint():
    files = json.loads((prompt_assembly.ROOT / 'config/runtime-files.json').read_text())
    assert 'dsherp/prompt_assembly.py' in files
