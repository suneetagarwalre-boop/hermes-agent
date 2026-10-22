"""Pure selection over the server-verified Reside host inventory, never new grants.

All supplied ID/title/date constraints intersect before any aggregate request.
Saved selections are server-produced resource bindings, not model/user prose.
"""
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

from gateway import reside_business as business

ZONE = ZoneInfo('America/Los_Angeles')
MONTHS = ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec')
NAMED_DATE = re.compile(
    r'\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|'
    r'Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s*'
    r'([0-9]{1,2})(?:,?\s+(20[0-9]{2}))?\b', re.IGNORECASE)
STOP_WORDS = set(('the for have webinar webinars registered registration registrations registrants '
    'people how many what which happening number count total current now today tomorrow yesterday '
    'reside january february march april may june july august september october november december '
    'jan feb mar apr jun jul aug sep oct nov dec').split())
FOLLOWUP = re.compile(
    r"(?:how many(?: (?:people|registrations|registrants))?(?: (?:have )?registered)?"
    r"(?: (?:now|today|tomorrow))?|what(?:'s| is) (?:the )?"
    r"(?:(?:current|registration|registrant|updated) )*(?:count|number|total)"
    r"(?: (?:now|today|tomorrow))?)\??", re.IGNORECASE)
CLARIFY = ('The title/ID and date do not identify the same single verified webinar, or '
    'the previous selection is missing, changed or expired. Specify one Reside webinar '
    'title/ID and date; no registration count is claimed.')


def local_day(timestamp):
    value = datetime.fromisoformat(timestamp)
    if value.tzinfo is None:
        raise ValueError('timezone required for webinar selection')
    return value.astimezone(ZONE).date()


def dates(text):
    """Retain EVERY date restriction, including invalid dates (reject, not erase)."""
    constraints = []
    for raw in re.findall(r'\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b', text):
        value = date.fromisoformat(raw)
        constraints.append((value.month, value.day, value.year))
    for month, day, year in NAMED_DATE.findall(text):
        month = MONTHS.index(month[:3].lower()) + 1
        # Leap-year validation does not silently impose a year on omitted years.
        date(int(year) if year else 2000, month, int(day))
        constraints.append((month, int(day), int(year) if year else None))
    today = local_day(business.now())
    for word, offset in (('today', 0), ('tomorrow', 1), ('yesterday', -1)):
        if re.search(r'\b' + word + r'\b', text, re.IGNORECASE):
            value = today + timedelta(days=offset)
            constraints.append((value.month, value.day, value.year))
    return constraints


def binding(row):
    return {'account': business.ZOOM_ACCOUNT, 'host': business.ZOOM_HOST,
        'id': row['id'], 'title': row['title'], 'start': row['start'],
        'intent': 'webinar_registration_aggregate'}


def valid_binding(value):
    return (isinstance(value, dict)
        and set(value) == {'account', 'host', 'id', 'title', 'start', 'intent'}
        and value['account'] == business.ZOOM_ACCOUNT and value['host'] == business.ZOOM_HOST
        and value['intent'] == 'webinar_registration_aggregate'
        and isinstance(value['id'], str) and re.fullmatch(r'[0-9]{9,12}', value['id']) is not None
        and isinstance(value['title'], str) and isinstance(value['start'], str))


def match(text, rows, bound=None):
    """Caller text can only narrow verified rows, never select a new account/path."""
    try:
        constraints = dates(text)
    except ValueError:
        return None
    ids = set(re.findall(r'\b[0-9]{9,12}\b', text))
    if len(ids) > 1 or (bound is not None and not valid_binding(bound)):
        return None
    words = set(re.findall(r'[a-z]{3,}', text.lower())) - STOP_WORDS
    scores = [(len(words & set(re.findall(r'[a-z]{3,}', r['title'].lower()))), r) for r in rows]
    best = max((score for score, _ in scores), default=0)
    # No arbitrary "first upcoming" default; an unbound request needs a selector.
    if not (ids or constraints or best >= 2 or bound):
        return None
    found = []
    for score, row in scores:
        if ids and row['id'] not in ids:
            continue
        if best >= 2 and score != best:
            continue
        if bound is not None and binding(row) != bound:
            continue
        if constraints:
            try:
                day = local_day(row['start'])
            except (TypeError, ValueError):
                continue
            if any((month, number) != (day.month, day.day) or (year is not None and year != day.year)
                    for month, number, year in constraints):
                continue
        found.append(row)
    return found[0] if len(found) == 1 else None


def count_question(text):
    return (re.search(r'\b(?:how many|number|count|total|registered for)\b', text, re.IGNORECASE)
        and re.search(r'\b(?:registrations?|registered|registrants?)\b', text, re.IGNORECASE)
        and re.search(r'\bwebinar\b', text, re.IGNORECASE)) is not None


def followup(text):
    return FOLLOWUP.fullmatch(text.strip()) is not None
