"""这次运行的模型看到了什么，由这里装配；改动模板文本必须同时递增 PROMPT_VERSION。

版本号是给人查的：精确复现靠 runtime_revision（本文件在 config/runtime-files.json 内，逐字节进指纹）。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 模板文本每变一次就 +1。改了模板却不改这里，等于把两次不同的装配记成同一个版本。
PROMPT_VERSION = '1'

_FRONTMATTER = re.compile(r'\A---\r?\n(.*?)\r?\n---\r?\n', re.DOTALL)


def _frontmatter(text):
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(':')
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def skill_summary(domain, root=ROOT):
    """本轮领域已装载技能的 name/version/description，取自被 sha256 钉住的清单与 frontmatter。

    读不出来就抛：一次装配不明的运行是不可复现的运行。清单与正文的版本必须一致——它们不一致
    时，run 上记的版本号会指向一份不是这次真的装进去的正文。
    """
    name = 'erp-' + str(domain)
    try:
        manifest = json.loads((Path(root) / 'config/business-skills.json').read_text(encoding='utf-8'))
        rows = manifest.get('skills') or []
        listed = next((row for row in rows if isinstance(row, dict) and row.get('name') == name), None)
        if listed is None:
            raise ValueError(f'业务技能摘要装载失败：{name} 不在清单里')
        body = (Path(root) / 'business-skills' / name / 'SKILL.md').read_text(encoding='utf-8')
    except OSError as error:
        raise ValueError(f'业务技能摘要装载失败：{name}（{type(error).__name__}）') from error
    fields = _frontmatter(body)
    description = fields.get('description') or ''
    version = fields.get('version') or ''
    if fields.get('name') != name or not description:
        raise ValueError(f'业务技能摘要装载失败：{name} 的 frontmatter 缺 name 或 description')
    if version != listed.get('version'):
        raise ValueError(f'业务技能摘要装载失败：{name} 正文版本 {version!r} 与清单 {listed.get("version")!r} 不符')
    return {'name': name, 'version': version, 'description': description}


def sampling_note():
    """dsherp 从不设置采样参数，请求体里没有 temperature（llm-deepseek 只在它有值时才发）。

    记录这一事实本身，而不是记录一个我们没有设过的数。
    """
    return 'provider-default'
