"""Deterministic event constraints; all dates use the Reside business timezone."""
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

BUSINESS_ZONE = ZoneInfo('America/Los_Angeles')
_MONTHS = 'Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?'
_NAMED = re.compile(r'\b(' + _MONTHS + r')\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b', re.IGNORECASE)
_MONTH_INDEX = {m: i for i, m in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split(), 1)}
_ISO = re.compile(r'(?<![\d/-])\b\d{4}-\d{2}-\d{2}\b(?![\d/-])')
_US_DATE = re.compile(r'(?<![\d/])\b(\d{1,2})/(\d{1,2})/(\d{4})\b(?![\d/])')
_RELATIVE = re.compile(r'\b(today|tomorrow|yesterday)\b', re.IGNORECASE)
_DATE_CONTINUATION = re.compile(r'\s*(?:[^\w\s]|\b(?:to|through)\b)\s*\d', re.IGNORECASE)
# This is a bounded selector grammar, not a natural-language date parser. Any
# remaining calendar-like qualifier must clarify, never compete as title words.
_TEMPORAL_RESIDUE = re.compile(
    r'\b(?:' + _MONTHS + r')(?=\d)|'
    r'\b(?:' + _MONTHS + r'|Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|'
    r'Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?|last|next|previous|before|after|since|until|'
    r'ago|days?|weeks?|months?|years?|tonight|morning|afternoon|evening|night)\b|'
    r'\b\d{4}\b|\b\d{1,4}[/.\-]\d{1,4}\b', re.IGNORECASE)
_WORDS = re.compile(r'[a-z]{3,}')
_STOP = set(('the for have has webinar registered registration registrations registrants people '
             'how many what which happening number count total october oct tomorrow today reside '
             'and now current currently updated please give tell about this that event its with '
             'are get can you does our date scheduled happening on approved pending denied').split())
_STOP.update(m.lower() for m in ('January February March April May June July August September October November December').split())
_STOP.update(_MONTH_INDEX)
FOLLOWUP = re.compile(r'^(?:(?:and\s+)?(?:now|currently)|(?:how\s+many|what(?:\s+is|[’\x27]s)?\s+(?:the\s+)?(?:registration\s+)?(?:count|total|number))(?:\s+.*)?)\s*[?.!]*$', re.IGNORECASE)


def date_constraints(text, now):
    """Return every constraint; unsupported temporal syntax raises ValueError."""
    return _date_parts(text, now)[0]


def _date_parts(text, now):
    """Parse and consume the same spans so ranking cannot erase restrictions.

    Slash dates are US month/day/four-digit-year. Other numeric conventions,
    incomplete dates and unsupported relative phrases require clarification.
    """
    constraints = []
    today = datetime.fromisoformat(now).astimezone(BUSINESS_ZONE).date()

    def consume(found):
        # Check before erasing the supported prefix: "October 22-25" must
        # not become a standalone date plus an otherwise harmless "-25".
        if _DATE_CONTINUATION.match(found.string[found.end():]):
            raise ValueError('unsupported temporal qualifier; clarify the event date')
        value = found.group()
        if found.re is _RELATIVE:
            d = today + timedelta(days={'today': 0, 'tomorrow': 1, 'yesterday': -1}[value.lower()])
            y, m, day = d.year, d.month, d.day
        elif found.re is _ISO:
            d = date.fromisoformat(value)
            y, m, day = d.year, d.month, d.day
        elif found.re is _US_DATE:
            m, day, y = map(int, found.groups())
        else:
            month, day, year = found.groups()
            m, day, y = _MONTH_INDEX[month[:3].lower()], int(day), int(year) if year else None
        date(y if y is not None else 2000, m, day)
        constraints.append((y, m, day))
        return ' '

    cleaned = text
    for pattern in (_RELATIVE, _ISO, _US_DATE, _NAMED):
        cleaned = pattern.sub(consume, cleaned)
    if _TEMPORAL_RESIDUE.search(cleaned):
        raise ValueError('unsupported temporal qualifier; clarify the event date')
    return constraints, cleaned


def event_day(start):
    stamp = datetime.fromisoformat(start)
    if stamp.tzinfo is None:
        raise ValueError('event timestamp must include timezone')
    return stamp.astimezone(BUSINESS_ZONE).date()


def match(text, rows, now, fallback_id=None):
    """Intersect title, explicit IDs and ALL date constraints before selecting."""
    try:
        constraints, cleaned = _date_parts(text, now)
    except ValueError:
        return None
    ids = set(re.findall(r'\b\d{9,12}\b', text))
    if len(ids) > 1:
        return None
    words = set(_WORDS.findall(cleaned.lower())) - _STOP
    # Rank independently over the full granted inventory so an ID cannot hide a
    # competing explicit title. A tie remains ambiguous until dates/ID intersect.
    scores = {r['id']: len(words & set(_WORDS.findall(r['title'].lower()))) for r in rows}
    best = max(scores.values(), default=0)
    if words and best < 2:
        return None
    candidates = []
    for row in rows:
        # Context is a binding selection, not a default another selector can replace.
        if fallback_id is not None and row['id'] != fallback_id:
            continue
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
