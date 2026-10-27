"""Pure syntax for captured M08 dependency declarations.

This parser neither resolves targets nor compares pins, policy, approval or time.
Callers retain responsibility for classification and a confined dependency grant.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import re
from typing import TYPE_CHECKING
import unicodedata

if TYPE_CHECKING:
    from .lint import Note

MAX_DEPENDENCY_BYTES = 16 * 1024
MAX_DEPENDENCIES = 32
MAX_TARGET_BYTES = 512
MAX_SLUG_CHARS = 64
IMPACT_FORMAT = 'whykit.impact-dependencies/v1'

_NAMESPACES = {'evidence': 'evidence_raw_sha256', 'claim': 'claim_markdown_sha256',
               'decision': 'decision_record_sha256', 'document': 'document_raw_sha256'}
_REQUIRED = {'title', 'type', 'status', 'owner', 'created', 'last_updated', 'source_of_truth', 'sensitivity'}
_CRITICAL = _REQUIRED | {'provenance', 'whykit_impact', 'claim_id', 'outcome_id', 'decision_id'}
_KEY_DELETE = str.maketrans('', '', '_- \t\r\n')


def _invalid() -> ValueError:
    return ValueError('invalid_impact_record')


def _key(value: object) -> str:
    return value.strip().casefold().translate(_KEY_DELETE) if isinstance(value, str) else ''


def _markers(front: dict) -> list[tuple]:
    """Inspect only namespaced root/immediate-provenance keys.

    Provenance is open to other producers. Never descend into their values or
    interpret generic artifact_kind/dependency keys as WhyKit declarations.
    """
    found: list[tuple[str, ...]] = []
    for key, value in front.items():
        normalized = _key(key)
        if normalized == 'whykitimpact':
            found.append((key,))
        if normalized == 'provenance' and isinstance(value, dict):
            found.extend((key, child) for child in value if _key(child) == 'whykitimpact')
    return found


def _reserved(path: str, name: str) -> bool:
    parts = path.replace('\\', '/').casefold().split('/')
    return any(parts[index:index + 2] == ['00-context', name] for index in range(len(parts) - 1))


def is_impact_note(note: Note) -> bool:
    """Pure conservative marker; a marker is neither valid syntax nor a grant."""
    return _reserved(note.path.as_posix(), 'implementation') or bool(_markers(note.front))


def _path(value: str) -> str:
    if (not value or value.startswith('/') or re.match(r'^[A-Za-z]:', value) or '\\' in value
            or any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs'} for char in value)
            or any(part in {'', '.', '..'} for part in value.split('/'))
            or not value.endswith(('.md', '.MD'))):
        raise _invalid()
    value.encode('utf-8')
    return value


def _card_path(value: str) -> None:
    if not value.startswith('00-context/implementation/') or not value.endswith('.md'):
        raise _invalid()
    parts = value.split('/')[2:]
    for index, part in enumerate(parts):
        slug = part[:-3] if index == len(parts) - 1 else part
        if re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', slug) is None:
            raise _invalid()


def _identity(value: str, prefix: str) -> None:
    if re.fullmatch(re.escape(prefix) + r'-[0-9]{3,15}', value) is None:
        raise _invalid()
    number = int(value[2:])
    if number < 1 or value != f'{prefix}-{number:03d}':
        raise _invalid()


def _target(value: object) -> tuple[str, str]:
    if not isinstance(value, str) or len(value.encode('utf-8')) > MAX_TARGET_BYTES:
        raise _invalid()
    kind, separator, target = value.partition(':')
    if not separator or kind not in _NAMESPACES:
        raise _invalid()
    if kind == 'document':
        _path(target)
        if _reserved(target, 'implementation'):
            _card_path(target)
    else:
        _identity(target, {'evidence': 'E', 'claim': 'C', 'decision': 'D'}[kind])
    return kind, value


def _front(note: Note) -> None:
    front = note.front
    if note.front_error or not note.has_front or not _REQUIRED <= front.keys():
        raise _invalid()
    critical = {_key(key): key for key in _CRITICAL}
    for key in front:
        if _key(key) in critical and key != critical[_key(key)]:
            raise _invalid()
    for key in ('title', 'owner'):
        if not isinstance(front[key], str) or not front[key].strip():
            raise _invalid()
        front[key].encode('utf-8')
    if front['status'] not in ('template', 'draft', 'in_review', 'approved', 'superseded', 'archived'):
        raise _invalid()
    if type(front['source_of_truth']) is not bool or front['sensitivity'] not in ('public', 'internal', 'confidential', 'restricted'):
        raise _invalid()
    for key in ('created', 'last_updated'):
        value = front[key]
        if not isinstance(value, str) or re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value) is None:
            raise _invalid()
        dt.date.fromisoformat(value)


def parse_impact_note(note: Note, root: Path) -> dict | None:
    """Return captured canonical syntax, None for ordinary notes, or fixed refusal.

    Paths are lexical identities. This function never resolves a symlink or opens
    any note, source, index or policy; even a syntactically valid pin is unverified.
    """
    try:
        markers = _markers(note.front)
        reserved = _reserved(note.path.as_posix(), 'implementation')
        if not markers and not reserved:
            return None
        _front(note)
        if any(path != ('provenance', 'whykit_impact') for path in markers):
            raise _invalid()
        if any(_reserved(note.path.as_posix(), name) for name in ('claims', 'outcomes')):
            raise _invalid()
        if (note.front.get('type') in ('claim', 'outcome') or 'claim_id' in note.front or 'outcome_id' in note.front
                or 'whykit_impact' in note.front):
            raise _invalid()
        relative = note.path.relative_to(root).as_posix()
        _path(relative)
        if len(relative.encode('utf-8')) > MAX_TARGET_BYTES:
            raise _invalid()
        producer = note.front.get('provenance')
        if not isinstance(producer, dict) or 'whykit_impact' not in producer:
            raise _invalid()
        scalar = producer['whykit_impact']
        if not isinstance(scalar, str) or len(scalar.encode('utf-8')) > MAX_DEPENDENCY_BYTES:
            raise _invalid()
        from .artifact_store import _strict_json
        declaration = _strict_json(scalar)
        if (not isinstance(declaration, dict) or set(declaration) != {'format', 'artifact_kind', 'dependencies'}
                or declaration['format'] != IMPACT_FORMAT):
            raise _invalid()
        kind = declaration['artifact_kind']
        if reserved:
            _card_path(relative)
            if (kind not in ('spec', 'pull-request', 'task') or note.front['sensitivity'] != 'restricted'
                    or note.front['type'] != ('specification' if kind == 'spec' else 'reference')):
                raise _invalid()
        else:
            from .knowledge_visibility import canonical_decision_id
            own_id = canonical_decision_id(note, root)
            if own_id is None or kind is not None:
                raise _invalid()
            _identity(own_id, 'D')
        entries = declaration['dependencies']
        if not isinstance(entries, list) or len(entries) > MAX_DEPENDENCIES:
            raise _invalid()
        dependencies = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'relation', 'target', 'pin'}:
                raise _invalid()
            if entry['relation'] not in ('requires', 'implements', 'measures'):
                raise _invalid()
            target_kind, target = _target(entry['target'])
            pin = entry['pin']
            if pin is not None:
                if (not isinstance(pin, dict) or set(pin) != {'namespace', 'sha256'}
                        or pin['namespace'] != _NAMESPACES[target_kind] or not isinstance(pin['sha256'], str)
                        or re.fullmatch(r'[0-9a-f]{64}', pin['sha256']) is None):
                    raise _invalid()
                pin = dict(pin)
            dependencies.append({'relation': entry['relation'], 'target': target, 'pin': pin})
        return {'path': relative, 'artifact_kind': kind, 'dependencies': tuple(dependencies)}
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise _invalid() from None
