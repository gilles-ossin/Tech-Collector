import hashlib
import html
import json
import re
import time
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser

import feedparser
import requests


# ============================================================
# CONFIGURATION
# ============================================================

ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT_DIR / "Datas" / "Events.json"

USER_AGENT = "AfricanTechCollector/1.0"
HEADERS = {"User-Agent": USER_AGENT}

REQUEST_DELAY = 1.0          # pause entre deux pages d'un même site (secondes)
MAX_PAGES_PER_SOURCE = 40    # fiches suivies depuis une page d'agenda
ARTICLES_PER_FEED = 50
KEEP_PAST_EVENTS = False     # True = conserver aussi les événements passés
RESPECT_ROBOTS_TXT = True

# ------------------------------------------------------------
# Annuaires d'événements (pages HTML)
#   urls   : pages d'agenda à lire
#   follow : regex sur le chemin des liens vers les fiches événement
#            (None = on lit seulement les événements présents sur la page)
#   africa_only : True = tout ce qui vient de cette source est africain
#   enabled : False pour désactiver sans supprimer
# Les données sont lues en priorité via le balisage schema.org (JSON-LD),
# que la plupart des sites d'événements exposent pour Google.
# ------------------------------------------------------------
DIRECTORIES = [
    {
        "name": "Liners",
        "urls": ["https://liners.com/events"],
        "follow": r"^/events/[a-z0-9-]+$",
        "africa_only": True,
        "enabled": True,
    },
    {
        "name": "AppsAfrica",
        "urls": ["https://appsafrica.com/events/", "https://www.appsafrica.com/events/list/"],
        "follow": None,
        "africa_only": True,
        "enabled": True,
    },
    {
        "name": "Tech.Africa",
        "urls": ["https://tech.africa/events/"],
        "follow": r"^/events/.+",
        "africa_only": True,
        "enabled": True,
    },
    {
        "name": "dev.events",
        "urls": [
            "https://dev.events/AF/tech",
            "https://dev.events/hackathons/AF",
            "https://dev.events/meetups/AF",
        ],
        "follow": r"^/(conferences|hackathons|meetups)/.+",
        "africa_only": True,
        "enabled": True,
    },
    # À compléter avec les liens "Agenda" / "Site officiel" de ta liste :
    {"name": "Africa Tech Events", "urls": [], "follow": None, "africa_only": True, "enabled": False},
    {"name": "Pavillon Numérique d'Afrique", "urls": [], "follow": None, "africa_only": True, "enabled": False},
    {"name": "AfriTechHub", "urls": [], "follow": None, "africa_only": True, "enabled": False},
    {"name": "TechAfrika", "urls": [], "follow": None, "africa_only": True, "enabled": False},
]

# ------------------------------------------------------------
# Médias (flux RSS) : les événements y sont annoncés dans les articles.
# Un article n'est retenu que s'il contient un mot-clé "événement",
# un mot-clé africain ET une date future dans son titre/résumé.
# ------------------------------------------------------------
FEEDS = [
    {"name": "TechCabal", "url": "https://techcabal.com/feed/"},
    {"name": "Techpoint Africa", "url": "https://techpoint.africa/feed/"},
]

EVENT_KEYWORDS = [
    "hackathon", "conference", "conférence", "summit", "sommet", "forum",
    "meetup", "workshop", "atelier", "webinar", "webinaire", "festival",
    "expo", "salon", "bootcamp", "masterclass", "demo day", "devfest",
    "pitch", "formation", "training", "congress", "congrès",
]

AFRICA_KEYWORDS = [
    "africa", "african", "afrique", "africain", "africaine",
    "nigeria", "kenya", "ghana", "ivory coast", "cote d'ivoire", "côte d'ivoire",
    "senegal", "sénégal", "rwanda", "uganda", "tanzania", "cameroon", "cameroun",
    "ethiopia", "south africa", "afrique du sud", "morocco", "maroc", "egypt",
    "egypte", "égypte", "tunisia", "tunisie", "algeria", "algérie", "togo",
    "benin", "bénin", "mali", "burkina faso", "mozambique", "zambia",
    "zimbabwe", "botswana", "namibia", "mauritius", "madagascar", "libya",
    "lagos", "abuja", "nairobi", "abidjan", "accra", "dakar", "kigali",
    "kampala", "cape town", "johannesburg", "cairo", "casablanca", "marrakech",
    "tunis", "kinshasa", "douala", "yaounde", "yaoundé", "lome", "lomé",
    "cotonou", "addis ababa",
]

TECH_KEYWORDS = [
    "technology", "tech", "startup", "software", "developer", "ai",
    "artificial intelligence", "machine learning", "fintech", "blockchain",
    "web3", "api", "cloud", "data", "cybersecurity", "digital", "mobile",
    "innovation", "open source", "robotics", "saas", "e-commerce", "telecom",
    "ict", "devops", "numérique", "intelligence artificielle",
]

# Type d'événement deviné d'après le texte (premier qui correspond gagne)
EVENT_TYPES = [
    ("Hackathon", ["hackathon"]),
    ("Meetup", ["meetup"]),
    ("Webinaire", ["webinar", "webinaire"]),
    ("Workshop", ["workshop", "atelier", "masterclass"]),
    ("Formation", ["bootcamp", "formation", "training"]),
    ("Salon", ["expo", "salon", "festival"]),
    ("Conférence", ["conference", "conférence", "summit", "sommet", "forum", "congress", "congrès", "devfest"]),
]

MONTHS = {
    "january": 1, "jan": 1, "janvier": 1,
    "february": 2, "feb": 2, "février": 2, "fevrier": 2, "févr": 2,
    "march": 3, "mar": 3, "mars": 3,
    "april": 4, "apr": 4, "avril": 4, "avr": 4,
    "may": 5, "mai": 5,
    "june": 6, "jun": 6, "juin": 6,
    "july": 7, "jul": 7, "juillet": 7, "juil": 7,
    "august": 8, "aug": 8, "août": 8, "aout": 8,
    "september": 9, "sep": 9, "sept": 9, "septembre": 9,
    "october": 10, "oct": 10, "octobre": 10,
    "november": 11, "nov": 11, "novembre": 11,
    "december": 12, "dec": 12, "décembre": 12, "decembre": 12, "déc": 12,
}


# ============================================================
# UTILITAIRES
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_html(value):
    if not value:
        return ""
    value = re.sub(r"<(script|style).*?</\1>", " ", str(value), flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def slugify(value):
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def normalize_url(url):
    """Supprime les paramètres de tracking d'une URL."""
    if not url:
        return ""

    parsed = urlparse(url.strip())
    ignored = {"utm_source", "utm_medium", "utm_campaign", "utm_term",
               "utm_content", "fbclid", "gclid"}
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k.lower() not in ignored]

    return urlunparse((
        parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"),
        "", urlencode(query), "",
    ))


_robots_cache = {}


def allowed_by_robots(url):
    if not RESPECT_ROBOTS_TXT:
        return True

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    if base not in _robots_cache:
        parser = RobotFileParser()
        try:
            response = SESSION.get(f"{base}/robots.txt", timeout=15)
            parser.parse(response.text.splitlines() if response.status_code == 200 else [])
        except requests.RequestException:
            parser.parse([])
        _robots_cache[base] = parser

    return _robots_cache[base].can_fetch(USER_AGENT, url)


def fetch(url):
    """Télécharge une page ; renvoie le texte ou None."""
    if not allowed_by_robots(url):
        print(f"[ROBOTS] Accès refusé par robots.txt : {url}")
        return None

    try:
        response = SESSION.get(url, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException as error:
        print(f"[ERREUR] {url} : {error}")
        return None


# ============================================================
# MOTS-CLÉS (q) ET PERTINENCE
# ============================================================

def _pattern(keyword):
    """Mot entier uniquement (évite que 'ai' matche 'said')."""
    return re.compile(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])")


EVENT_PATTERNS = [(k, _pattern(k)) for k in EVENT_KEYWORDS]
AFRICA_PATTERNS = [(k, _pattern(k)) for k in AFRICA_KEYWORDS]
TECH_PATTERNS = [(k, _pattern(k)) for k in TECH_KEYWORDS]


def find_terms(text, patterns):
    return [k for k, p in patterns if p.search(text)]


def match_terms(text):
    text = text.lower()
    return (
        find_terms(text, EVENT_PATTERNS),
        find_terms(text, AFRICA_PATTERNS),
        find_terms(text, TECH_PATTERNS),
    )


def guess_type(text):
    text = text.lower()
    for label, words in EVENT_TYPES:
        if any(_pattern(w).search(text) for w in words):
            return label
    return "Conférence"


# ============================================================
# DATES
# ============================================================

_MONTH_ALT = "|".join(sorted((re.escape(m) for m in MONTHS), key=len, reverse=True))

# "November 16 - 19, 2026" / "Nov 5, 2026"
DATE_EN = re.compile(
    rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*[-–]\s*\d{{1,2}})?,?\s+(\d{{4}})",
    re.I,
)
# "16 - 19 novembre 2026" / "5 Nov 2026"
DATE_FR = re.compile(
    rf"\b(\d{{1,2}})(?:er)?(?:\s*[-–]\s*\d{{1,2}})?\s+(?:de\s+)?({_MONTH_ALT})\.?,?\s+(\d{{4}})",
    re.I,
)


def extract_dates(text):
    found = []

    for month, day, year in DATE_EN.findall(text):
        found.append((int(year), MONTHS[month.lower()], int(day)))

    for day, month, year in DATE_FR.findall(text):
        found.append((int(year), MONTHS[month.lower()], int(day)))

    dates = []
    for year, month, day in found:
        try:
            dates.append(date(year, month, day))
        except ValueError:
            continue

    return sorted(dates)


def first_upcoming_date(text):
    today = date.today()
    for found in extract_dates(text):
        if found >= today or KEEP_PAST_EVENTS:
            return found.isoformat()
    return None


def iso_day(value):
    """'2026-11-15T09:00:00+01:00' -> '2026-11-15'."""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", str(value or ""))
    return match.group(1) if match else None


# ============================================================
# EXTRACTION schema.org / JSON-LD
# ============================================================

JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
HREF_RE = re.compile(r'<a[^>]+href=["\']([^"\'#]+)', re.I)


def walk_events(node):
    if isinstance(node, list):
        for item in node:
            yield from walk_events(item)
    elif isinstance(node, dict):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]

        if any(isinstance(t, str) and t.endswith("Event") for t in types):
            yield node

        for key in ("@graph", "itemListElement", "item", "subEvent"):
            if key in node:
                yield from walk_events(node[key])


def iter_jsonld_events(page):
    for raw in JSONLD_RE.findall(page):
        try:
            data = json.loads(html.unescape(raw.strip()))
        except ValueError:
            continue
        yield from walk_events(data)


def as_text(value):
    if isinstance(value, dict):
        return clean_html(value.get("name") or value.get("@value") or "")
    if isinstance(value, list):
        return as_text(value[0]) if value else ""
    return clean_html(value or "")


def parse_location(node):
    mode = str(node.get("eventAttendanceMode", "")).lower()
    raw = node.get("location")
    places = raw if isinstance(raw, list) else [raw]

    parts, online = [], False

    for place in places:
        if isinstance(place, str):
            parts.append(clean_html(place))
            continue
        if not isinstance(place, dict):
            continue
        if "VirtualLocation" in str(place.get("@type", "")):
            online = True
            continue

        address = place.get("address")
        city = country = None

        if isinstance(address, dict):
            city = address.get("addressLocality")
            country = address.get("addressCountry")
            if isinstance(country, dict):
                country = country.get("name")
        elif isinstance(address, str):
            city = address

        label = ", ".join(str(x) for x in (city or place.get("name"), country) if x)
        if label:
            parts.append(clean_html(label))

    text = ", ".join(dict.fromkeys(parts))

    if "mixed" in mode:
        return f"{text} & Hybride" if text else "Hybride"
    if (online or "online" in mode) and not text:
        return "En ligne"
    return text or None


def parse_image(node):
    image = node.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    return image if isinstance(image, str) and image.startswith("http") else None


# ============================================================
# CONSTRUCTION DE L'ÉVÉNEMENT
# ============================================================

def build_event(title, description, event_date, location, link,
                image, organizer, attendees, event_type, q):
    now = now_iso()
    slug = slugify(title) or hashlib.md5(link.encode()).hexdigest()[:12]

    return {
        "title": title,
        "slug": slug,
        "is_active": False,
        "description": description[:1000],
        "event_date": event_date,
        "location": location,
        "type": event_type,
        "organizer_username": None,
        "organizer_name": organizer or None,
        "link": link,
        "attendees_count": str(attendees) if attendees else None,
        "img": [image] if image else [],
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "eventDate": event_date,
        "q": q,
    }


def event_from_jsonld(node, page_url, listing_url):
    title = as_text(node.get("name"))
    event_date = iso_day(node.get("startDate"))

    if not title or not event_date:
        return None

    description = as_text(node.get("description"))
    location = parse_location(node)
    link = normalize_url(urljoin(page_url, node.get("url") or page_url))

    organizer = as_text(node.get("organizer"))
    attendees = node.get("maximumAttendeeCapacity")

    text = f"{title} {description} {location or ''}"
    event_terms, africa_terms, tech_terms = match_terms(text)
    q = event_terms + africa_terms + tech_terms or [listing_url]

    return build_event(
        title, description, event_date, location, link,
        parse_image(node), organizer, attendees,
        guess_type(text), q,
    )


def event_from_article(entry, source_name):
    title = clean_html(entry.get("title", ""))
    link = normalize_url(entry.get("link", ""))

    if not title or not link:
        return None

    description = clean_html(entry.get("summary", ""))
    text = f"{title} {description}"

    event_terms, africa_terms, tech_terms = match_terms(text)
    if not (event_terms and africa_terms):
        return None

    event_date = first_upcoming_date(text)
    if not event_date:
        return None

    image = None
    for media in entry.get("media_content", []) + entry.get("media_thumbnail", []):
        if media.get("url"):
            image = media["url"]
            break

    return build_event(
        title, description, event_date, africa_terms[0].title(), link,
        image, source_name, None, guess_type(text),
        event_terms + africa_terms + tech_terms,
    )


def is_relevant(event, africa_only):
    if not event["event_date"]:
        return False

    if not KEEP_PAST_EVENTS and event["event_date"] < date.today().isoformat():
        return False

    if africa_only:
        return True

    text = f"{event['title']} {event['description']} {event['location'] or ''}"
    return bool(match_terms(text)[1])


# ============================================================
# COLLECTEURS
# ============================================================

def collect_from_directory(source):
    print(f"\n[AGENDA] {source['name']}...")
    found = []

    for listing in source["urls"]:
        page = fetch(listing)
        if not page:
            continue

        pairs = [(listing, node) for node in iter_jsonld_events(page)]

        if source.get("follow"):
            pattern = re.compile(source["follow"])
            host = urlparse(listing).netloc
            links = []

            for href in HREF_RE.findall(page):
                absolute = normalize_url(urljoin(listing, href))
                parsed = urlparse(absolute)

                if parsed.netloc == host and pattern.search(parsed.path):
                    links.append(absolute)

            links = [l for l in dict.fromkeys(links) if l != normalize_url(listing)]
            print(f"[INFO] {len(links)} fiches repérées sur {listing}")

            for link in links[:MAX_PAGES_PER_SOURCE]:
                time.sleep(REQUEST_DELAY)
                sub_page = fetch(link)
                if sub_page:
                    pairs += [(link, node) for node in iter_jsonld_events(sub_page)]

        for page_url, node in pairs:
            event = event_from_jsonld(node, page_url, listing)
            if event and is_relevant(event, source.get("africa_only", False)):
                found.append(event)

        time.sleep(REQUEST_DELAY)

    print(f"[INFO] {len(found)} événements retenus.")
    return found


def collect_from_feed(source):
    print(f"\n[RSS] {source['name']}...")

    try:
        response = SESSION.get(source["url"], timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        print(f"[ERREUR] {source['name']} : {error}")
        return []

    feed = feedparser.parse(response.content)
    found = []

    for entry in feed.entries[:ARTICLES_PER_FEED]:
        event = event_from_article(entry, source["name"])
        if event and is_relevant(event, africa_only=True):
            found.append(event)

    print(f"[INFO] {len(found)} événements retenus.")
    return found


# ============================================================
# GESTION DU JSON
# ============================================================

def load_database():
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    if not OUTPUT_FILE.exists():
        return {"events": []}

    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(f"Impossible de lire {OUTPUT_FILE}: {error}")

    if isinstance(data, list):
        return {"events": data}
    if not isinstance(data, dict):
        return {"events": []}
    if not isinstance(data.get("events"), list):
        data["events"] = []

    return data


def save_database(data):
    temp_file = OUTPUT_FILE.with_suffix(".tmp")

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    temp_file.replace(OUTPUT_FILE)


def dedup_key(title, event_date):
    """Un même événement est souvent listé par plusieurs sources."""
    return f"{slugify(title)}|{event_date}"


def get_next_id(events):
    ids = [e.get("id") for e in events if isinstance(e.get("id"), int)]
    return max(ids, default=0) + 1


def generate_unique_slug(slug, existing_slugs):
    if slug not in existing_slugs:
        return slug

    index = 2
    while f"{slug}-{index}" in existing_slugs:
        index += 1

    return f"{slug}-{index}"


# ============================================================
# COLLECTEUR PRINCIPAL
# ============================================================

def run_collector():
    print("=" * 55)
    print(" AFRICAN TECH - EVENT COLLECTOR")
    print("=" * 55)

    database = load_database()
    events = database["events"]

    existing_links = {normalize_url(e.get("link", "")) for e in events if e.get("link")}
    existing_keys = {dedup_key(e.get("title", ""), e.get("event_date")) for e in events}
    existing_slugs = {e.get("slug") for e in events if e.get("slug")}
    next_id = get_next_id(events)

    discovered = []

    for source in DIRECTORIES:
        if source.get("enabled") and source.get("urls"):
            discovered += collect_from_directory(source)

    for source in FEEDS:
        discovered += collect_from_feed(source)

    added = 0

    for event in discovered:
        link = normalize_url(event["link"])
        key = dedup_key(event["title"], event["event_date"])

        if link in existing_links or key in existing_keys:
            continue

        event["id"] = next_id
        event["slug"] = generate_unique_slug(event["slug"], existing_slugs)

        events.append(event)

        existing_links.add(link)
        existing_keys.add(key)
        existing_slugs.add(event["slug"])

        next_id += 1
        added += 1

        print(f"[NOUVEAU] {event['event_date']} - {event['title']}")

    if added:
        save_database(database)
        print(f"\n[OK] {added} nouvel(aux) événement(s) enregistré(s).")
    else:
        print("\n[INFO] Aucun nouvel événement à ajouter.")

    print(f"[TOTAL] {len(events)} événements dans le JSON.")
    print(f"[FICHIER] {OUTPUT_FILE}")


if __name__ == "__main__":
    run_collector()
