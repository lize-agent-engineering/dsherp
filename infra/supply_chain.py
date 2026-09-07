#!/usr/bin/env python3
"""Bill of materials and vulnerability gate for the two release images.

The tools are deliberately not vendored: this module builds the exact commands and
decides pass or fail, so the decision is testable offline and the same in CI as by hand.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

from dsherp import deploy_env


ROOT = deploy_env.ROOT
SEVERITIES = ('negligible', 'low', 'medium', 'high', 'critical')
DEFAULT_THRESHOLD = 'high'
INSTALL_HINT = {
    'syft': 'brew install syft 或见 https://github.com/anchore/syft',
    'grype': 'brew install grype 或见 https://github.com/anchore/grype',
}


def require(tool):
    if not shutil.which(tool):
        raise SystemExit(f'缺少 {tool}：{INSTALL_HINT[tool]}')
    return tool


def sbom_command(image, target):
    return ['syft', image, '-o', 'cyclonedx-json', '--file', str(target)]


def scan_command(image, threshold=DEFAULT_THRESHOLD):
    if threshold not in SEVERITIES:
        raise ValueError('未知严重级别：' + repr(threshold))
    return ['grype', image, '-o', 'json', '--fail-on', threshold]


def blocking_findings(report, threshold=DEFAULT_THRESHOLD):
    """Findings at or above the threshold, with a fix available or not — both block."""
    if threshold not in SEVERITIES:
        raise ValueError('未知严重级别：' + repr(threshold))
    floor = SEVERITIES.index(threshold)
    matches = report.get('matches') if isinstance(report, dict) else None
    if not isinstance(matches, list):
        raise ValueError('无法解析的扫描结果；不要当作通过')
    blocking = []
    for match in matches:
        vulnerability = match.get('vulnerability') or {}
        severity = str(vulnerability.get('severity', '')).lower()
        if severity in SEVERITIES and SEVERITIES.index(severity) >= floor:
            blocking.append({'id': vulnerability.get('id'), 'severity': severity,
                             'package': (match.get('artifact') or {}).get('name'),
                             'fixed_in': (vulnerability.get('fix') or {}).get('versions') or []})
    return blocking


def architectures(image, *, runner=subprocess.run):
    """Record which platforms a pinned digest actually serves; G1 targets linux/amd64."""
    result = runner(['docker', 'manifest', 'inspect', image], text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise SystemExit('无法读取镜像架构清单；不要在未知架构上发布')
    payload = json.loads(result.stdout)
    entries = payload.get('manifests') or [payload]
    found = []
    for entry in entries:
        platform = entry.get('platform') or {}
        if platform.get('architecture') in (None, 'unknown'):
            continue
        found.append(f"{platform.get('os')}/{platform['architecture']}")
    return sorted(set(found))


def main(argv=None):
    parser = argparse.ArgumentParser(description='发布镜像的 SBOM 与漏洞门')
    parser.add_argument('--threshold', choices=SEVERITIES, default=DEFAULT_THRESHOLD)
    parser.add_argument('--out', type=Path, default=ROOT / 'work' / 'supply-chain')
    parser.add_argument('--architectures-only', action='store_true')
    arguments = parser.parse_args(argv)
    resolved = deploy_env.settings()
    images = [resolved['frappe_image'], resolved['worker_image']]
    if arguments.architectures_only:
        print(json.dumps({image: architectures(image) for image in {deploy_env.BASE_IMAGE, *images}},
                         ensure_ascii=False, indent=2))
        return 0
    require('syft')
    require('grype')
    arguments.out.mkdir(parents=True, exist_ok=True)
    failures = []
    for image in images:
        name = re.sub('[^A-Za-z0-9._-]', '_', image)
        subprocess.run(sbom_command(image, arguments.out / f'{name}.cdx.json'), check=True, timeout=1800)
        scan = subprocess.run(scan_command(image, arguments.threshold), text=True,
                              capture_output=True, timeout=1800)
        (arguments.out / f'{name}.grype.json').write_text(scan.stdout)
        blocking = blocking_findings(json.loads(scan.stdout), arguments.threshold)
        if blocking:
            failures.append({'image': image, 'blocking': blocking})
    print(json.dumps({'threshold': arguments.threshold, 'failures': failures}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
