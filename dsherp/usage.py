"""What a run actually cost (T5), computed from what the server itself recorded.

The provider's own numbers arrive as `model_response` events - runtime/model-guard.cjs
reports each finished chunk's `usage` - and the run's own claim/finish events give its
duration. Aggregating here rather than trusting a runner's summary means an under-reporting
or crashed runner shows up as a missing number (`usage_unknown_calls`), never as a cheap run.

Pure functions: the Frappe side feeds them rows, the CLI feeds them rows from every Site."""
import json

INPUT_KEYS = ('input_tokens', 'prompt_tokens', 'input', 'prompt')
OUTPUT_KEYS = ('output_tokens', 'completion_tokens', 'output', 'completion')
REQUEST_KEYS = ('request_id', 'id', 'provider_request_id')
FINISHED_STATUSES = ('Succeeded', 'Failed', 'Cancelled')


def _payload(event):
    raw = event.get('payload')
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or '{}')
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _number(source, keys):
    for key in keys:
        value = source.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
    return None


def _moment(text):
    from datetime import datetime
    for shape in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(str(text)[:26], shape)
        except (TypeError, ValueError):
            continue
    return None


def summarise(events):
    """The metering fields for one run, from its own event stream."""
    models, requests = [], []
    input_tokens = output_tokens = unknown = 0
    skills = None
    first = last = None
    for event in events:
        kind = event.get('kind')
        moment = _moment(event.get('recorded_at'))
        if moment is not None:
            first = moment if first is None else min(first, moment)
            if kind == 'finished':
                last = moment if last is None else max(last, moment)
        payload = _payload(event)
        if kind == 'runtime_started' and isinstance(payload.get('skill_versions'), dict):
            skills = payload['skill_versions']
        if kind != 'model_response':
            continue
        model = payload.get('model')
        if isinstance(model, str) and model and model not in models:
            models.append(model)
        usage = payload.get('usage')
        request = _text(payload, REQUEST_KEYS) or (_text(usage, REQUEST_KEYS) if isinstance(usage, dict) else None)
        if request and request not in requests:
            requests.append(request)
        if not isinstance(usage, dict):
            unknown += 1
            continue
        given = False
        for keys, add in ((INPUT_KEYS, 'input'), (OUTPUT_KEYS, 'output')):
            value = _number(usage, keys)
            if value is None:
                continue
            given = True
            if add == 'input':
                input_tokens += value
            else:
                output_tokens += value
        if not given:
            unknown += 1
    duration = None
    if first is not None and last is not None:
        duration = max(int((last - first).total_seconds() * 1000), 0)
    return {'model': ','.join(models), 'provider_request_ids': json.dumps(requests),
            'actual_input_tokens': input_tokens, 'actual_output_tokens': output_tokens,
            'duration_ms': duration, 'skill_versions': json.dumps(skills) if skills is not None else json.dumps(None),
            'usage_unknown_calls': unknown}


def storable(summary):
    """The same numbers, in the shape a DocType row accepts.

    `duration_ms` is an Int column and a run that never finished has no duration; writing the
    row must not turn "unknown" into a null the column refuses, nor into a zero that would
    read as "instant". The field is left out, so the row keeps whatever it had."""
    return {key: value for key, value in summary.items() if value is not None}


def _text(source, keys):
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _utc_month(created, zone):
    """Frappe stores a datetime in the Site's own system timezone; a bill spans Sites, so the
    month is decided in UTC. A Site that did not say which zone it uses is read as UTC and
    reported as such rather than quietly bucketed."""
    from datetime import timezone as _timezone
    moment = _moment(created)
    if moment is None:
        return None, False
    if not zone or zone == 'UTC':
        return moment.strftime('%Y-%m'), not zone
    try:
        from zoneinfo import ZoneInfo
        local = moment.replace(tzinfo=ZoneInfo(zone))
    except Exception:
        return moment.strftime('%Y-%m'), True
    return local.astimezone(_timezone.utc).strftime('%Y-%m'), False


def monthly(rows, month):
    """Usage per Site for one calendar month. A run that has not finished is counted as
    unfinished rather than billed: its numbers are not finished either."""
    sites = {}
    assumed = set()
    for row in rows:
        bucket, assumed_utc = _utc_month(row.get('creation'), row.get('time_zone'))
        if assumed_utc:
            assumed.add(row.get('site') or '')
        if bucket != month:
            continue
        site = sites.setdefault(row.get('site') or '', {
            'runs': 0, 'succeeded': 0, 'failed': 0, 'cancelled': 0, 'unfinished': 0,
            'input_tokens': 0, 'output_tokens': 0, 'model_calls': 0, 'duration_ms': 0})
        status = row.get('status')
        if status not in FINISHED_STATUSES:
            site['unfinished'] += 1
            continue
        site['runs'] += 1
        site[{'Succeeded': 'succeeded', 'Failed': 'failed', 'Cancelled': 'cancelled'}[status]] += 1
        site['input_tokens'] += int(row.get('actual_input_tokens') or 0)
        site['output_tokens'] += int(row.get('actual_output_tokens') or 0)
        site['model_calls'] += int(row.get('model_calls') or 0)
        site['duration_ms'] += int(row.get('duration_ms') or 0)
    totals = {key: sum(site[key] for site in sites.values())
              for key in ('runs', 'succeeded', 'failed', 'cancelled', 'unfinished',
                          'input_tokens', 'output_tokens', 'model_calls', 'duration_ms')}
    return {'month': month, 'sites': sites, 'totals': totals,
            'assumed_utc': sorted(site for site in assumed if site in sites)}
