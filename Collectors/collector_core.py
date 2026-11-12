"""
Moteur commun des collecteurs "externes" (communautés, espaces...).

Même approche que Parser-event.py :
  - annuaires HTML : balisage schema.org (JSON-LD) en priorité, puis repli sur
    les balises meta/OpenGraph des fiches, ou extraction de listes dans des articles ;
  - flux RSS des médias (optionnel) ;
  - mots-clés (champ "q"), robots.txt, pause entre requêtes, dédoublonnage.

Chaque collecteur (Parser-community.py, Parser-space.py) fournit un "profil" :
les fonctions qui transforment une page en enregistrement au bon schéma.
"""

import hashlib
import html
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser

import feedparser
import requests


# ============================================================
# RÉGLAGES (modifiables depuis les collecteurs : core.REQUEST_DELAY = 2)
# ============================================================

USER_AGENT = "AfricanTechCollector/1.0"
REQUEST_DELAY = 1.0          # pause entre deux pages d'un même site (secondes)
MAX_PAGES_PER_SOURCE = 60    # fiches suivies par source
ENTRIES_PER_FEED = 50
RESPECT_ROBOTS_TXT = True

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


# ============================================================
# UTILITAIRES
# ============================================================

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
    value = unicodedata.normalize("NFKD", value or "")
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


def unique_slug(slug, existing):
    if slug not in existing:
        return slug

    index = 2
    while f"{slug}-{index}" in existing:
        index += 1

    return f"{slug}-{index}"


def host_of(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


# ============================================================
# MOTS-CLÉS (q)
# ============================================================

def make_patterns(words):
    """Mot entier uniquement (évite que 'ai' matche 'said')."""
    return [
        (w, re.compile(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])"))
        for w in words
    ]


def find_terms(text, patterns):
    text = (text or "").lower()
    return [w for w, p in patterns if p.search(text)]



# ============================================================
# LIEUX AFRICAINS (détection + libellé affiché)
# ============================================================

COUNTRIES = {
    "nigeria": "Nigeria", "kenya": "Kenya", "ghana": "Ghana",
    "ivory coast": "Côte d'Ivoire", "cote d'ivoire": "Côte d'Ivoire", "côte d'ivoire": "Côte d'Ivoire",
    "senegal": "Sénégal", "sénégal": "Sénégal", "rwanda": "Rwanda", "uganda": "Ouganda",
    "tanzania": "Tanzanie", "cameroon": "Cameroun", "cameroun": "Cameroun",
    "ethiopia": "Éthiopie", "south africa": "Afrique du Sud", "afrique du sud": "Afrique du Sud",
    "morocco": "Maroc", "maroc": "Maroc", "egypt": "Égypte", "egypte": "Égypte", "égypte": "Égypte",
    "tunisia": "Tunisie", "tunisie": "Tunisie", "algeria": "Algérie", "algérie": "Algérie",
    "togo": "Togo", "benin": "Bénin", "bénin": "Bénin", "mali": "Mali",
    "burkina faso": "Burkina Faso", "mozambique": "Mozambique", "zambia": "Zambie",
    "zimbabwe": "Zimbabwe", "botswana": "Botswana", "namibia": "Namibie",
    "mauritius": "Maurice", "madagascar": "Madagascar", "libya": "Libye",
    "gabon": "Gabon", "guinea": "Guinée", "guinée": "Guinée", "niger": "Niger",
    "dr congo": "RD Congo", "drc": "RD Congo", "rd congo": "RD Congo",
    "sierra leone": "Sierra Leone", "liberia": "Liberia", "malawi": "Malawi",
    "sudan": "Soudan", "somalia": "Somalie", "angola": "Angola", "gambia": "Gambie",
}

CITIES = {
    "lagos": "Lagos, Nigeria", "abuja": "Abuja, Nigeria", "port harcourt": "Port Harcourt, Nigeria",
    "ibadan": "Ibadan, Nigeria", "kano": "Kano, Nigeria",
    "nairobi": "Nairobi, Kenya", "mombasa": "Mombasa, Kenya",
    "accra": "Accra, Ghana", "kumasi": "Kumasi, Ghana",
    "abidjan": "Abidjan, Côte d'Ivoire", "dakar": "Dakar, Sénégal",
    "kigali": "Kigali, Rwanda", "kampala": "Kampala, Ouganda",
    "dar es salaam": "Dar es Salaam, Tanzanie", "douala": "Douala, Cameroun",
    "yaoundé": "Yaoundé, Cameroun", "yaounde": "Yaoundé, Cameroun",
    "addis ababa": "Addis-Abeba, Éthiopie",
    "cape town": "Le Cap, Afrique du Sud", "johannesburg": "Johannesburg, Afrique du Sud",
    "pretoria": "Pretoria, Afrique du Sud", "durban": "Durban, Afrique du Sud",
    "casablanca": "Casablanca, Maroc", "rabat": "Rabat, Maroc", "marrakech": "Marrakech, Maroc",
    "cairo": "Le Caire, Égypte", "tunis": "Tunis, Tunisie", "algiers": "Alger, Algérie",
    "lomé": "Lomé, Togo", "lome": "Lomé, Togo", "cotonou": "Cotonou, Bénin",
    "ouagadougou": "Ouagadougou, Burkina Faso", "bamako": "Bamako, Mali",
    "kinshasa": "Kinshasa, RD Congo", "lubumbashi": "Lubumbashi, RD Congo",
    "libreville": "Libreville, Gabon", "antananarivo": "Antananarivo, Madagascar",
    "maputo": "Maputo, Mozambique", "lusaka": "Lusaka, Zambie", "harare": "Harare, Zimbabwe",
    "gaborone": "Gaborone, Botswana", "windhoek": "Windhoek, Namibie",
}

AFRICA_GENERIC = [
    "africa", "african", "africans", "afrique", "africain", "africaine", "africains",
    "sub-saharan", "west africa", "east africa", "afrique de l'ouest",
]

PLACES = {**COUNTRIES, **CITIES}
_PLACE_PATTERNS = sorted(make_patterns(list(PLACES)), key=lambda wp: -len(wp[0]))
AFRICA_PATTERNS = make_patterns(AFRICA_GENERIC) + _PLACE_PATTERNS


def find_place(text):
    """Premier lieu africain cité dans le texte, au libellé d'affichage (ou None)."""
    text = (text or "").lower()
    best = None

    for term, pattern in _PLACE_PATTERNS:
        match = pattern.search(text)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), term)

    return PLACES[best[1]] if best else None


def africa_terms(text):
    return find_terms(text, AFRICA_PATTERNS)


# ============================================================
# RÉSEAU
# ============================================================

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
# EXTRACTION : JSON-LD (schema.org)
# ============================================================

JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


def walk_nodes(node, types):
    if isinstance(node, list):
        for item in node:
            yield from walk_nodes(item, types)
    elif isinstance(node, dict):
        found = node.get("@type")
        found = found if isinstance(found, list) else [found]

        if any(t in types for t in found if isinstance(t, str)):
            yield node

        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            if key in node:
                yield from walk_nodes(node[key], types)


def iter_jsonld(page, types):
    for raw in JSONLD_RE.findall(page):
        try:
            data = json.loads(html.unescape(raw.strip()))
        except ValueError:
            continue
        yield from walk_nodes(data, types)


def as_text(value):
    if isinstance(value, dict):
        return clean_html(value.get("name") or value.get("@value") or "")
    if isinstance(value, list):
        return as_text(value[0]) if value else ""
    return clean_html(value or "")


def jsonld_place(node):
    """Renvoie (ville, pays) d'un nœud schema.org, quand ils existent."""
    raw = node.get("address") or node.get("location")
    raw = raw[0] if isinstance(raw, list) and raw else raw

    if isinstance(raw, dict):
        raw = raw.get("address", raw)

    if isinstance(raw, dict):
        country = raw.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        return clean_html(raw.get("addressLocality")) or None, clean_html(country) or None

    return None, None


def jsonld_image(node):
    image = node.get("image") or node.get("logo")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    return image if isinstance(image, str) and image.startswith("http") else None


# ============================================================
# EXTRACTION : balises meta, liens, images
# ============================================================

def meta_content(page, prop):
    patterns = [
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, page, re.I)
        if match:
            return clean_html(match.group(1))
    return None


def page_title(page):
    """Titre de la page sans le suffixe du site ('Anzisha - AfriLabs' -> 'Anzisha')."""
    title = meta_content(page, "og:title")

    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
        title = clean_html(match.group(1)) if match else ""

    return re.split(r"\s+[-|–—•]\s+", title)[0].strip()


def page_text(page):
    return clean_html(page)


ANCHOR_RE = re.compile(r'<a\b[^>]*?href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>', re.I | re.S)
IMG_RE = re.compile(r'<img\b[^>]*?(?:data-src|src)=["\']([^"\']+)["\']', re.I)
HEADING_RE = re.compile(r"<h([2-4])\b[^>]*>(.*?)</h\1>", re.I | re.S)
PARA_RE = re.compile(r"<(?:p|li)\b[^>]*>(.*?)</(?:p|li)>", re.I | re.S)

# Liens qui ne désignent jamais une communauté ou un lieu
IGNORED_LINK_PARTS = (
    "sharer", "intent/tweet", "share?", "share=", "mailto:", "tel:", "javascript:",
    "wp-login", "/tag/", "/category/", "/author/", "whatsapp.com/send",
)


def external_links(chunk, page_url):
    """Liens sortants d'un fragment HTML : [(url, texte)]."""
    here = host_of(page_url)
    links = []

    for href, text in ANCHOR_RE.findall(chunk):
        absolute = urljoin(page_url, href.strip())

        if not absolute.startswith(("http://", "https://")):
            continue
        if host_of(absolute) == here:
            continue
        if any(part in absolute.lower() for part in IGNORED_LINK_PARTS):
            continue

        links.append((normalize_url(absolute), clean_html(text)))

    return links


def visit_link(page, page_url, pattern):
    """Premier lien sortant dont le texte correspond (ex. 'Visit website')."""
    if not pattern:
        return None

    regex = re.compile(pattern, re.I)
    for url, text in external_links(page, page_url):
        if regex.search(text):
            return url
    return None


def article_body(page):
    match = re.search(r"<article\b.*?</article>", page, re.I | re.S)
    return match.group(0) if match else page


def iter_sections(page, page_url):
    """Découpe un article en sections (un titre h2-h4 + son contenu)."""
    body = article_body(page)
    heads = list(HEADING_RE.finditer(body))

    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(body)
        chunk = body[head.start():end]
        content = body[head.end():end]

        paragraphs = [clean_html(p) for p in PARA_RE.findall(content)]
        paragraphs = [p for p in paragraphs if len(p) >= 20]

        yield {
            "heading": clean_html(head.group(2)),
            "paragraphs": paragraphs,
            "text": " ".join(paragraphs),
            "links": external_links(chunk, page_url),
            "images": [
                urljoin(page_url, src) for src in IMG_RE.findall(chunk)
                if not src.startswith("data:")
            ],
        }


# ============================================================
# GESTION DU JSON
# ============================================================

def load_database(path, root_key):
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        return {root_key: []}

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(f"Impossible de lire {path}: {error}")

    if isinstance(data, list):
        return {root_key: data}
    if not isinstance(data, dict):
        return {root_key: []}
    if not isinstance(data.get(root_key), list):
        data[root_key] = []

    return data


def save_database(path, data):
    temp_file = path.with_suffix(".tmp")

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    temp_file.replace(path)


# ============================================================
# COLLECTE
# ============================================================

def expand_listings(source):
    """
    Pages d'agenda d'une source. Chaque listing est un dict :
      url | url_template ("...page/{n}") + max_pages, country (optionnel).
    Les simples chaînes de source["urls"] sont acceptées.
    """
    listings = [{"url": u} if isinstance(u, str) else dict(u) for u in source.get("urls", [])]
    return listings


def collect_from_directory(source, profile):
    print(f"\n[ANNUAIRE] {source['name']}...")

    mode = source.get("mode", "directory")
    follow = re.compile(source["follow"]) if source.get("follow") else None

    visited = set()           # fiches déjà lues pour cette source
    fetched_pages = 0
    entries = []              # (record, page_url, origin)

    for listing in expand_listings(source):
        template = listing.get("url_template")
        urls = (
            (template.format(n=n) for n in range(1, listing.get("max_pages", 1) + 1))
            if template else iter([listing["url"]])
        )

        for url in urls:
            page = fetch(url)
            time.sleep(REQUEST_DELAY)

            if not page:
                break  # fin de pagination (404) ou page inaccessible

            local = dict(source, country=listing.get("country", source.get("country")))

            # 1) Article-liste : une section = une entrée
            if mode == "article_list":
                for section in iter_sections(page, url):
                    record = profile["record_from_section"](section, url, local)
                    if record:
                        entries.append((record, url, "section"))
                continue

            # 2) Entités déclarées dans la page elle-même (JSON-LD)
            for node in iter_jsonld(page, profile["ld_types"]):
                record = profile["record_from_ld"](node, url, local)
                if record:
                    entries.append((record, url, "ld"))

            if not follow:
                continue

            # 3) Fiches détaillées
            links = []
            for href, _ in ANCHOR_RE.findall(page):
                absolute = normalize_url(urljoin(url, href.strip()))
                parsed = urlparse(absolute)

                if host_of(absolute) == host_of(url) and follow.search(parsed.path):
                    links.append(absolute)

            new_links = [
                l for l in dict.fromkeys(links)
                if l not in visited and l != normalize_url(url)
            ]
            print(f"[INFO] {len(new_links)} nouvelles fiches sur {url}")

            if template and not new_links:
                break  # la pagination ne renvoie plus rien de neuf

            for link in new_links:
                if fetched_pages >= MAX_PAGES_PER_SOURCE:
                    break

                visited.add(link)
                fetched_pages += 1
                time.sleep(REQUEST_DELAY)

                sub_page = fetch(link)
                if not sub_page:
                    continue

                nodes = list(iter_jsonld(sub_page, profile["ld_types"]))
                records = [profile["record_from_ld"](n, link, local) for n in nodes]
                records = [r for r in records if r]

                if records:
                    entries += [(r, link, "ld") for r in records]
                elif profile.get("record_from_page"):
                    record = profile["record_from_page"](sub_page, link, local)
                    if record:
                        entries.append((record, link, "page"))

    # Une organisation qui figure sur presque toutes les fiches du site
    # est son éditeur (ex. "AfriLabs" en pied de page), pas une entrée.
    pages_by_name = {}
    for record, page_url, origin in entries:
        if origin == "ld":
            pages_by_name.setdefault(record["name"].lower(), set()).add(page_url)

    found = [
        record for record, page_url, origin in entries
        if not (origin == "ld" and len(pages_by_name[record["name"].lower()]) > 2)
        and profile["is_relevant"](record, source)
    ]

    print(f"[INFO] {len(found)} entrées retenues.")
    return found


def collect_from_feed(source, profile):
    print(f"\n[RSS] {source['name']}...")

    if not profile.get("record_from_entry"):
        return []

    try:
        response = SESSION.get(source["url"], timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        print(f"[ERREUR] {source['name']} : {error}")
        return []

    feed = feedparser.parse(response.content)
    found = []

    for entry in feed.entries[:ENTRIES_PER_FEED]:
        record = profile["record_from_entry"](entry, source["name"])
        if record and profile["is_relevant"](record, source):
            found.append(record)

    print(f"[INFO] {len(found)} entrées retenues.")
    return found


def run(profile):
    print("=" * 55)
    print(f" AFRICAN TECH - {profile['title'].upper()}")
    print("=" * 55)

    output = profile["output"]
    root_key = profile["root_key"]

    database = load_database(output, root_key)
    items = database[root_key]

    known_links = {normalize_url(i.get("link", "")) for i in items if i.get("link")}
    known_keys = {profile["dedup_key"](i) for i in items}
    known_slugs = {i.get("slug") for i in items if i.get("slug")}
    next_id = max([i["id"] for i in items if isinstance(i.get("id"), int)], default=0) + 1

    discovered = []

    for source in profile["directories"]:
        if source.get("enabled") and source.get("urls"):
            discovered += collect_from_directory(source, profile)

    for source in profile.get("feeds", []):
        if source.get("enabled"):
            discovered += collect_from_feed(source, profile)

    added = 0

    for record in discovered:
        link = normalize_url(record["link"])
        key = profile["dedup_key"](record)

        if link in known_links or key in known_keys:
            continue

        record["id"] = next_id
        record["slug"] = unique_slug(record["slug"], known_slugs)

        items.append(record)
        known_links.add(link)
        known_keys.add(key)
        known_slugs.add(record["slug"])

        next_id += 1
        added += 1
        print(f"[NOUVEAU] {record['name']} - {record['location']}")

    if added:
        database["last_update"] = now_iso()
        save_database(output, database)
        print(f"\n[OK] {added} nouvelle(s) entrée(s) enregistrée(s).")
    else:
        print("\n[INFO] Aucune nouvelle entrée à ajouter.")

    print(f"[TOTAL] {len(items)} entrées dans le JSON.")
    print(f"[FICHIER] {output}")
