import json
import re
import html
import hashlib
import unicodedata
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests
import feedparser


# ============================================================
# CONFIGURATION
# ============================================================

ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT_DIR / "Datas" / "Articles.json"

SOURCES = [
    {
        "name": "TechCabal",
        "url": "https://techcabal.com/feed/",
    },
    {
        "name": "Techpoint Africa",
        "url": "https://techpoint.africa/feed/",
    },
]

ARTICLES_PER_SOURCE = 50
MIN_SCORE = 1

HEADERS = {
    "User-Agent": "AfricanTechCollector/1.0"
}

AFRICA_KEYWORDS = [
    "africa", "african", "afrique",
    "nigeria", "kenya", "ghana",
    "ivory coast", "cote d'ivoire",
    "senegal", "rwanda", "uganda",
    "tanzania", "cameroon", "ethiopia",
    "south africa", "morocco", "egypt",
    "lagos", "nairobi", "abidjan",
    "accra", "dakar", "kigali", maroc
]

TECH_KEYWORDS = [
    "technology", "tech", "startup",
    "software", "developer", "development",
    "artificial intelligence", "ai",
    "machine learning", "fintech",
    "blockchain", "api", "sdk",
    "programming", "cloud", "data",
    "cybersecurity", "digital",
    "mobile", "internet", "innovation",
    "open source", "robotics", "saas",
    "e-commerce", "telecom", "ict",
]


# ============================================================
# UTILITAIRES
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_html(value):
    """Supprime les balises HTML et nettoie le texte."""
    if not value:
        return ""

    value = re.sub(r"<(script|style).*?</\1>", " ", value,
                   flags=re.I | re.S)

    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def slugify(value):
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def normalize_url(url):
    """Supprime les paramètres de tracking d'une URL."""
    if not url:
        return ""

    parsed = urlparse(url.strip())

    ignored_params = [
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
    ]

    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query)
        if key.lower() not in ignored_params
    ]

    return urlunparse((
        parsed.scheme,
        parsed.netloc.lower(),
        parsed.path.rstrip("/"),
        "",
        urlencode(query),
        ""
    ))


def get_published_date(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")

    if parsed:
        from time import struct_time, strftime, gmtime
        return strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            parsed
        )

    return now_iso()


def get_categories(entry):
    categories = []

    for category in entry.get("tags", []):
        term = category.get("term", "").strip()

        if term:
            categories.append(term)

    # Suppression des doublons
    return list(dict.fromkeys(categories))


def get_image(entry):
    """Essaie de récupérer une image depuis les métadonnées RSS."""

    # Enclosure
    for enclosure in entry.get("enclosures", []):
        image_url = enclosure.get("href") or enclosure.get("url")

        if image_url and (
            "image" in enclosure.get("type", "")
            or re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", image_url, re.I)
        ):
            return image_url

    # Media RSS
    for key in ["media_content", "media_thumbnail"]:
        for media in entry.get(key, []):
            image_url = media.get("url")

            if image_url:
                return image_url

    # Contenu HTML
    content = entry.get("content", [])

    if content:
        source = content[0].get("value", "")
        match = re.search(
            r'<img[^>]+src=["\']([^"\']+)',
            source,
            re.I
        )

        if match:
            return match.group(1)

    # Image dans la description
    description = entry.get("summary", "")

    match = re.search(
        r'<img[^>]+src=["\']([^"\']+)',
        description,
        re.I
    )

    if match:
        return match.group(1)

    return None


# ============================================================
# PERTINENCE
# ============================================================

def calculate_score(title, description):
    text = f"{title} {description}".lower()

    score = 0

    if any(word in text for word in AFRICA_KEYWORDS):
        score += 1

    if any(word in text for word in TECH_KEYWORDS):
        score += 1

    return score


# ============================================================
# NORMALISATION DES ARTICLES
# ============================================================

def normalize_article(entry, source):
    title = clean_html(entry.get("title", ""))

    if not title:
        return None

    link = normalize_url(entry.get("link", ""))

    if not link:
        return None

    description = clean_html(
        entry.get("summary", "")
    )

    if not description:
        content = entry.get("content", [])

        if content:
            description = clean_html(
                content[0].get("value", "")
            )

    score = calculate_score(title, description)

    if score < MIN_SCORE:
        return None

    published_at = get_published_date(entry)

    author = entry.get("author", "").strip() or None

    image = get_image(entry)

    tags = get_categories(entry)

    # Ajout du nom de la source aux tags si nécessaire
    if source["name"] not in tags:
        tags.append(source["name"])

    slug = slugify(title)

    # Évite les slugs vides ou trop courts
    if not slug:
        slug = hashlib.md5(link.encode()).hexdigest()[:12]

    return {
        "title": title,
        "slug": slug,
        "is_active": False,
        "summary": description[:500],
        "content": {
            "title": title,
            "subtitle": "",
            "desc": description[:1000],
            "sections": []
        },
        "author_name": author,
        "author_username": None,
        "tags": tags,
        "link": link,
        "img": [image] if image else [],
        "status": "pending",
        "created_at": published_at,
        "updated_at": now_iso()
    }


# ============================================================
# GESTION DU JSON
# ============================================================

def load_database():
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    if not OUTPUT_FILE.exists():
        return {"articles": []}

    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            return {"articles": data}

        if not isinstance(data, dict):
            return {"articles": []}

        if not isinstance(data.get("articles"), list):
            data["articles"] = []

        return data

    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(
            f"Impossible de lire {OUTPUT_FILE}: {error}"
        )


def save_database(data):
    temp_file = OUTPUT_FILE.with_suffix(".tmp")

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )

    temp_file.replace(OUTPUT_FILE)


def get_existing_links(articles):
    return {
        normalize_url(article.get("link", ""))
        for article in articles
        if article.get("link")
    }


def get_next_id(articles):
    ids = [
        article.get("id")
        for article in articles
        if isinstance(article.get("id"), int)
    ]

    return max(ids, default=0) + 1


def generate_unique_slug(slug, existing_slugs):
    if slug not in existing_slugs:
        return slug

    index = 2

    while f"{slug}-{index}" in existing_slugs:
        index += 1

    return f"{slug}-{index}"


# ============================================================
# COLLECTEUR
# ============================================================

def collect_from_source(source):
    print(f"\n[RSS] Lecture de {source['name']}...")

    try:
        response = requests.get(
            source["url"],
            headers=HEADERS,
            timeout=30
        )
        response.raise_for_status()

        feed = feedparser.parse(response.content)

        if feed.bozo and not feed.entries:
            print(f"[ERREUR] Flux RSS invalide : {source['name']}")
            return []

        entries = feed.entries[:ARTICLES_PER_SOURCE]

        print(f"[INFO] {len(entries)} articles trouvés.")

        articles = []

        for entry in entries:
            article = normalize_article(entry, source)

            if article:
                articles.append(article)

        return articles

    except requests.RequestException as error:
        print(f"[ERREUR] {source['name']} : {error}")
        return []


def run_collector():
    print("=" * 55)
    print(" AFRICAN TECH - ARTICLE COLLECTOR")
    print("=" * 55)

    database = load_database()

    articles = database["articles"]

    existing_links = get_existing_links(articles)

    existing_slugs = {
        article.get("slug")
        for article in articles
        if article.get("slug")
    }

    next_id = get_next_id(articles)

    added = 0

    for source in SOURCES:
        discovered = collect_from_source(source)

        for article in discovered:
            link = normalize_url(article["link"])

            if link in existing_links:
                continue

            article["id"] = next_id

            article["slug"] = generate_unique_slug(
                article["slug"],
                existing_slugs
            )

            articles.append(article)

            existing_links.add(link)
            existing_slugs.add(article["slug"])

            next_id += 1
            added += 1

            print(f"[NOUVEAU] {article['title']}")

    if added:
        save_database(database)
        print(f"\n[OK] {added} nouvel(aux) article(s) enregistré(s).")
    else:
        print("\n[INFO] Aucun nouvel article à ajouter.")

    print(f"[TOTAL] {len(articles)} articles dans le JSON.")
    print(f"[FICHIER] {OUTPUT_FILE}")


if __name__ == "__main__":
    run_collector()
