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
MONTH_NAME = (
    r'Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|'
    r'Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?')
NAMED_DATE = re.compile(
    rf'\b({MONTH_NAME})\.?\s*([0-9]{{1,2}})(?:st|nd|rd|th)?\b'
    r'(?:(?:\s*,\s*|\s+)([0-9]+)\b)?', re.IGNORECASE)
NUMERIC_DATE = re.compile(r'\b[0-9]+(?:\s*[-/.]\s*[0-9]+){1,2}\b')
UNPARSED_DATE = re.compile(
    rf'\b(?:{MONTH_NAME})\.?(?=\s*(?:[0-9]|first\b|second\b|third\b|'
    r'fourth\b|fifth\b|sixth\b|seventh\b|eighth\b|ninth\b|tenth\b|'
    r'eleventh\b|twelfth\b|thirteenth\b|fourteenth\b|fifteenth\b|'
    r'sixteenth\b|seventeenth\b|eighteenth\b|nineteenth\b|twent|thirt))'
    rf'|\b(?:on|in|dated)\s+(?:{MONTH_NAME})\b'
    rf'|\b[0-9]{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTH_NAME})\b'
    r'|\b[0-9]{4}\b|,\s*[0-9]+\b', re.IGNORECASE)
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

    def named(found):
        month, day, year = found.groups()
        if year is not None and len(year) != 4:
            raise ValueError('explicit year requires four digits')
        month = MONTHS.index(month[:3].lower()) + 1
        # Leap-year validation does not silently impose a year on omitted years.
        date(int(year) if year else 2000, month, int(day))
        constraints.append((month, int(day), int(year) if year else None))
        return ' '

    def numeric(found):
        raw = found.group()
        if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', raw):
            value = date.fromisoformat(raw)
            constraints.append((value.month, value.day, value.year))
        else:
            slash = re.fullmatch(r'([0-9]{1,2})/([0-9]{1,2})(?:/([0-9]{4}))?', raw)
            if slash is None:
                raise ValueError('unsupported numeric date restriction')
            month, day, year = slash.groups()
            date(int(year) if year else 2000, int(month), int(day))
            constraints.append((int(month), int(day), int(year) if year else None))
        return ' '

    # Remove only fully parsed restrictions; residue must never turn into an
    # ID/title-only selection when a caller supplied an unsupported date.
    remaining = NUMERIC_DATE.sub(numeric, NAMED_DATE.sub(named, text))
    if UNPARSED_DATE.search(remaining):
        raise ValueError('unparsed date restriction')
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


def multiple_titles(text, rows):
    """Separate explicit title mentions cannot be resolved by overlap scores.

    Punctuation is not identity; a short title embedded in a longer one is
    not a second mention unless it also occurs separately in the request.
    """
    normalized = ' '.join(re.findall(r'[a-z0-9]+', text.lower()))
    mentions = []
    for row in rows:
        title = ' '.join(re.findall(r'[a-z0-9]+', row['title'].lower()))
        if len(title.split()) < 2:
            continue
        for found in re.finditer(r'\b' + re.escape(title) + r'\b', normalized):
            mentions.append((title, found.start(), found.end()))
    return any(left != right and (end <= other_start or other_end <= start)
        for left, start, end in mentions for right, other_start, other_end in mentions)


def match(text, rows, bound=None):
    """Caller text can only narrow verified rows, never select a new account/path."""
    try:
        constraints = dates(text)
    except ValueError:
        return None
    ids = set(re.findall(r'\b[0-9]{9,12}\b', text))
    if len(ids) > 1 or (bound is not None and not valid_binding(bound)) or multiple_titles(text, rows):
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
