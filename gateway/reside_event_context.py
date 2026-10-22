"""Deterministic event constraints; all dates use the Reside business timezone."""
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

BUSINESS_ZONE = ZoneInfo('America/Los_Angeles')
_MONTHS = 'Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?'
_NAMED = re.compile(r'\b(' + _MONTHS + r')\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b', re.IGNORECASE)
_MONTH_INDEX = {m: i for i, m in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split(), 1)}
_ISO = re.compile(r'\b\d{4}-\d{2}-\d{2}\b')
_WORDS = re.compile(r'[a-z]{3,}')
_STOP = set(('the for have has webinar registered registration registrations registrants people '
             'how many what which happening number count total october oct tomorrow today reside '
             'and now current currently updated please give tell about this that event its with '
             'are get can you does our date scheduled happening on').split())
_STOP.update(m.lower() for m in ('January February March April May June July August September October November December').split())
_STOP.update(_MONTH_INDEX)
FOLLOWUP = re.compile(r'^(?:(?:and\s+)?(?:now|currently)|(?:how\s+many|what(?:\s+is|[’\x27]s)?\s+(?:the\s+)?(?:registration\s+)?(?:count|total|number))(?:\s+.*)?)\s*[?.!]*$', re.IGNORECASE)


def date_constraints(text, now):
    """Return every constraint, not last-one-wins. Missing year stays unspecified."""
    constraints = []
    today = datetime.fromisoformat(now).astimezone(BUSINESS_ZONE).date()
    for word, offset in (('today', 0), ('tomorrow', 1)):
        if re.search(r'\b' + word + r'\b', text, re.IGNORECASE):
            d = today + timedelta(days=offset)
            constraints.append((d.year, d.month, d.day))
    for value in _ISO.findall(text):
        d = date.fromisoformat(value)
        constraints.append((d.year, d.month, d.day))
    for month, day, year in _NAMED.findall(text):
        m, d, y = _MONTH_INDEX[month[:3].lower()], int(day), int(year) if year else None
        date(y or 2000, m, d)  # Validate even a date absent from the authorized inventory.
        constraints.append((y, m, d))
    return constraints


def event_day(start):
    stamp = datetime.fromisoformat(start)
    if stamp.tzinfo is None:
        raise ValueError('event timestamp must include timezone')
    return stamp.astimezone(BUSINESS_ZONE).date()


def match(text, rows, now, fallback_id=None):
    """Intersect title, explicit IDs and ALL date constraints before selecting."""
    try:
        constraints = date_constraints(text, now)
    except ValueError:
        return None
    ids = set(re.findall(r'\b\d{9,12}\b', text))
    if len(ids) > 1:
        return None
    cleaned = _NAMED.sub(' ', _ISO.sub(' ', text))
    words = set(_WORDS.findall(cleaned.lower())) - _STOP
    # Rank independently over the full granted inventory so an ID cannot hide a
    # competing explicit title. A tie remains ambiguous until dates/ID intersect.
    scores = {r['id']: len(words & set(_WORDS.findall(r['title'].lower()))) for r in rows}
    best = max(scores.values(), default=0)
    if words and best < 2:
        return None
    candidates = []
    for row in rows:
        if ids and row['id'] not in ids:
            continue
        if words and scores[row['id']] != best:
            continue
        if constraints:
            if not row.get('start'):
                continue
            d = event_day(row['start'])
            if any((y is not None and y != d.year) or m != d.month or day != d.day
                   for y, m, day in constraints):
                continue
        candidates.append(row)
    if not (ids or words or constraints):
        candidates = [r for r in candidates if r['id'] == fallback_id]
    return candidates[0] if len(candidates) == 1 else None
