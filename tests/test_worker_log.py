import json

from dsherp import worker_log


def test_log_writes_one_json_line_with_redacted_secrets(capsys):
    worker_log.configure(['sk-synthetic-secret'])
    worker_log.log('claimed',run_id='r1',note='key=sk-synthetic-secret',count=2)
    line=capsys.readouterr().err.strip().splitlines()[-1]
    record=json.loads(line)
    assert record['event']=='claimed' and record['run_id']=='r1' and record['count']==2
    assert 'sk-synthetic-secret' not in line and '[redacted]' in record['note']
    assert set(record)>={'ts','event'}
