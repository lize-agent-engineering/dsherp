"""Both evaluation fixtures in one provisioning step.

`dev_stack` steps are named after what they guarantee, and what this one guarantees is that
the daily Site can carry the evaluation set: the injection carriers the injection cases read,
and the fixed synthetic documents the rebased and long-tail cases name. They are two modules
because they mean different things — one is an attack payload, the other is ordinary business
data a case must not confuse with it — but they are one provisioning step because a Site with
only half of them fails cases for reasons that have nothing to do with the Agent.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.setup import injection, rebased_records  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description='评估集在站上需要的两套夹具')
    parser.add_argument('--site', default='dsherp-daily.localhost')
    args = parser.parse_args(argv)
    injection.main(['--site', args.site])
    rebased_records.main(['--site', args.site])


if __name__ == '__main__':
    main()
