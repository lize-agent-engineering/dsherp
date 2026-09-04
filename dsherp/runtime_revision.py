"""Content identity of the selected runtime, never a model-supplied version."""
import hashlib
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FILES=tuple(json.loads((ROOT/'config/runtime-files.json').read_text()))
KEYS=('DEEPSEEK_API_KEY','DEEPSEEK_BASE_URL')


def verify_business_skills(root=ROOT):
    manifest=json.loads((root/'config/business-skills.json').read_text())
    if manifest.get('schema_version')!=1 or not isinstance(manifest.get('skills'),list) or not manifest['skills']:
        raise ValueError('Invalid business skill manifest')
    names=[row['name'] for row in manifest['skills']]
    if len(set(names))!=len(names) or any(not re.fullmatch('[a-z][a-z0-9-]*',name) for name in names):
        raise ValueError('Invalid skill catalog')
    directory=root/'business-skills'
    if directory.is_symlink():raise ValueError('Skill symbolic links are forbidden')
    if set(path.name for path in directory.iterdir())!=set(names):raise ValueError('Unexpected skill catalog')
    for row in manifest['skills']:
        folder=directory/row['name'];file=folder/'SKILL.md'
        if folder.is_symlink() or file.is_symlink():raise ValueError('Skill symbolic links are forbidden')
        if {path.name for path in folder.iterdir()}!={'SKILL.md'}:raise ValueError('Unexpected skill catalog files')
        content=file.read_bytes()
        if hashlib.sha256(content).hexdigest()!=row['sha256']:raise ValueError('Business skill digest mismatch')
        header=content.decode().split('---',2)[1]
        if f"name: {row['name']}" not in header.splitlines() or f"version: {row['version']}" not in header.splitlines():
            raise ValueError('Business skill version mismatch')


def configuration_revision(settings,root=ROOT):
    for key in KEYS:
        if not isinstance(settings.get(key),str) or not settings[key].strip():
            raise ValueError('Missing runtime configuration: '+key)
    verify_business_skills(root)
    files=[[name,hashlib.sha256((root/name).read_bytes()).hexdigest()] for name in FILES]
    # The digest binds credentials too, but never stores or exposes their value.
    value=[files,[settings[key] for key in KEYS]]
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
