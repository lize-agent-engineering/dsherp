"""The backup-set protocol: pure functions shared by the host CLI, the sync step and the drill.

A set is one stable state of one Site. Its data half (three pieces + the G2 snapshot +
set.json) and its secret half (site_config_backup.json + pair.json) live in different
volumes and different repositories, bound by one id and by the digests pair.json copies
from set.json. A restore never takes 'latest' from each side: it takes the newest set whose
two halves were both read back and agreed.

Digest conventions (one place, because a mismatch must be diagnosable):
  * every piece digest is the sha256 of the file's raw bytes (what `sha256sum` prints);
  * snapshot_sha256 is the sha256 of snapshot_text(snapshot) - the exact UTF-8 text written
    to snapshot.json, so the reader recomputes it from the file it just fetched;
  * set_sha256 is the sha256 of the canonical JSON of set.json (sorted keys, no spaces).
"""
import hashlib
import json
import re

FORMAT = 1
DATA_PIECES = ('database.sql.gz', 'files.tar', 'private-files.tar')
CONFIG_PIECE = 'site_config_backup.json'
KINDS = ('scheduled', 'release', 'retire')
STAMP = re.compile(r'\d{8}_\d{6}')
TOKEN = re.compile(r'[a-z0-9]{6}')
# A Site name is <label>(.<label>)*; labels may carry hyphens, so the slug may too. The id is
# therefore parsed by position, not by a character class that would reject dsherp-validation.
SLUG = re.compile(r'[a-z0-9][a-z0-9_-]*')
SHA256 = re.compile(r'[0-9a-f]{64}')
STAMP_LENGTH = 15
TOKEN_LENGTH = 6


def slug(site):
    return site.replace('.', '_')


def _checked_slug(site):
    value = slug(site)
    if not SLUG.fullmatch(value):
        raise ValueError('A Site name must be a domain name: ' + repr(site))
    return value


def new_set_id(site, stamp, token):
    if not STAMP.fullmatch(stamp or ''):
        raise ValueError('A set stamp is YYYYmmdd_HHMMSS (UTC): ' + repr(stamp))
    if not TOKEN.fullmatch(token or ''):
        raise ValueError('A set token is six lowercase alphanumerics: ' + repr(token))
    return f'{stamp}-{_checked_slug(site)}-{token}'


def parse_set_id(set_id):
    """By position: <15-char stamp>-<slug>-<6-char token>; the slug may contain hyphens."""
    if not isinstance(set_id, str) or len(set_id) < STAMP_LENGTH + TOKEN_LENGTH + 3:
        return None
    stamp, separator, rest = set_id[:STAMP_LENGTH], set_id[STAMP_LENGTH:STAMP_LENGTH + 1], set_id[STAMP_LENGTH + 1:]
    name, tail, token = rest[:-TOKEN_LENGTH - 1], rest[-TOKEN_LENGTH - 1:-TOKEN_LENGTH], rest[-TOKEN_LENGTH:]
    if separator != '-' or tail != '-' or not STAMP.fullmatch(stamp) or not TOKEN.fullmatch(token) or not SLUG.fullmatch(name):
        return None
    return {'stamp': stamp, 'slug': name, 'token': token}


def snapshot_text(snapshot):
    """The exact text snapshot.json holds; its sha256 is what set.json records."""
    return json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)


def snapshot_sha256(snapshot):
    return hashlib.sha256(snapshot_text(snapshot).encode()).hexdigest()


def set_manifest(*, set_id, site, kind, stamp, window, image_tag, image_id, frappe_version, pieces, snapshot_sha256):
    parsed = parse_set_id(set_id)
    if parsed is None or parsed['stamp'] != stamp or parsed['slug'] != slug(site):
        raise ValueError('set_id does not belong to this Site and stamp: ' + repr(set_id))
    if kind not in KINDS:
        raise ValueError('Unknown set kind: ' + repr(kind))
    if set(pieces) != set(DATA_PIECES):
        raise ValueError('A set records exactly the data pieces ' + ', '.join(DATA_PIECES) + ': ' + repr(sorted(pieces)))
    for name, row in pieces.items():
        if not SHA256.fullmatch(str(row.get('sha256'))) or type(row.get('bytes')) is not int or row['bytes'] < 0:
            raise ValueError('Piece needs a sha256 and a byte count: ' + name)
    if not SHA256.fullmatch(snapshot_sha256 or ''):
        raise ValueError('The snapshot digest is missing or malformed')
    # A restore has to prove it runs the build the backup was taken on, so the identity of that
    # build is part of the set, not something to discover afterwards.
    if not isinstance(image_tag, str) or not image_tag.strip():
        raise ValueError('A set records the image tag the Site ran')
    if not isinstance(image_id, str) or not image_id.startswith('sha256:') or len(image_id) <= len('sha256:'):
        raise ValueError('A set records the immutable image id the Site ran: ' + repr(image_id))
    return {'format': FORMAT, 'set_id': set_id, 'site': site, 'kind': kind, 'stamp': stamp,
            'window': {'started': window['started'], 'finished': window['finished']},
            'image_tag': image_tag, 'image_id': image_id, 'frappe_version': frappe_version,
            'pieces': {name: {'sha256': row['sha256'], 'bytes': row['bytes']} for name, row in pieces.items()},
            'snapshot_sha256': snapshot_sha256}


def set_sha256(set_doc):
    return hashlib.sha256(json.dumps(set_doc, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def pair_manifest(set_doc, *, config_sha256):
    """What the secret half carries so it can prove it belongs to this exact data half."""
    if not SHA256.fullmatch(config_sha256 or ''):
        raise ValueError('The site_config digest is missing or malformed')
    return {'format': FORMAT, 'set_id': set_doc['set_id'], 'site': set_doc['site'],
            'pieces': {name: row['sha256'] for name, row in set_doc['pieces'].items()},
            'snapshot_sha256': set_doc['snapshot_sha256'], 'config_sha256': config_sha256,
            'set_sha256': set_sha256(set_doc)}


def pair_matches(set_doc, pair_doc):
    try:
        return bool(pair_doc.get('format') == FORMAT
                    and pair_doc['set_id'] == set_doc['set_id'] and pair_doc['site'] == set_doc['site']
                    and pair_doc['pieces'] == {name: row['sha256'] for name, row in set_doc['pieces'].items()}
                    and pair_doc['snapshot_sha256'] == set_doc['snapshot_sha256']
                    and SHA256.fullmatch(str(pair_doc.get('config_sha256')))
                    and pair_doc['set_sha256'] == set_sha256(set_doc))
    except (KeyError, TypeError, AttributeError):
        return False


def local_prune(set_ids, keep=3, protect=()):
    """The set ids to delete locally: everything but the newest `keep`, never a protected one."""
    ordered = sorted(set_id for set_id in set_ids if parse_set_id(set_id))  # the stamp leads, so lexical == time
    if len(ordered) <= keep:
        return []
    return [set_id for set_id in ordered[:-keep] if set_id not in set(protect)]
