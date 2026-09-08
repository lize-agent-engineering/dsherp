#!/usr/bin/env python3
"""The highest value in a `docker stats` memory sample file, compared as a number.

The nightly samples the database's memory every 15 seconds so that an OOM has a curve behind
it rather than a guess. The first version reduced that curve with
`sort -t'\t' -k2 ... | tail -1`, which produced an empty file on every run: GNU sort accepts
only a single-character separator and exits 2 on a two-character '\t', the error went to
/dev/null and the exit code to `|| true`. Even with the separator fixed, `-k2` compares
"99.9MiB" against "625.5MiB" as text and calls the smaller one larger.

So the reduction is done here, on numbers, and a file it cannot read is an error rather than
an empty artefact that reads as "no problem".
"""
import sys
from pathlib import Path

UNITS = {'b': 1 / (1024 * 1024), 'k': 1 / 1024, 'kib': 1 / 1024, 'm': 1.0, 'mib': 1.0,
         'g': 1024.0, 'gib': 1024.0}


def mebibytes(used):
    """'625.5MiB' -> 625.5. Unknown units are refused, not guessed at."""
    text = used.strip()
    digits = text.rstrip('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
    unit = text[len(digits):].lower() or 'm'
    if unit not in UNITS:
        raise ValueError(f'不认识的内存单位：{text!r}')
    return float(digits) * UNITS[unit]


def peak(lines):
    """(MiB, the sample line) for the largest sample; (0.0, '') when nothing was sampled."""
    best = (0.0, '')
    for line in lines:
        parts = line.rstrip('\n').split('\t')
        if len(parts) < 2 or ' / ' not in parts[1]:
            continue
        try:
            value = mebibytes(parts[1].split(' / ')[0])
        except ValueError:
            continue
        if value > best[0]:
            best = (value, line.rstrip('\n'))
    return best


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else Path('work/db-mem.tsv')
    if not path.is_file():
        # Never an empty artefact: a missing curve has to say so, or it reads as "no problem".
        print(f'没有采样文件 {path}：采样步骤没跑，或者它写不进去', file=sys.stderr)
        return 1
    value, line = peak(path.read_text().splitlines())
    if not line:
        print(f'{path} 里没有可解析的采样行', file=sys.stderr)
        return 1
    print(f'峰值 {value:.1f} MiB  {line}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
