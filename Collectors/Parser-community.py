import re
from pathlib import Path

import collector_core as core


# ============================================================
# CONFIGURATION
# ============================================================

# Racine du repo = dossier parent de Collectors/
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = BASE_DIR / "Datas" / "Communities.json"

core.REQUEST_DELAY = 1.0
core.MAX_PAGES_PER_SOURCE = 60

# ------------------------------------------------------------
# Sources
#   mode "article_list" : article qui présente une liste de communautés
#                         (un titre h2-h4 + un paragraphe + un lien = une entrée)
#   mode "directory"    : annuaire avec fiches ; lecture du JSON-LD schema.org,
#                         sinon des balises meta de la fiche (voir "follow")
#   use_body            : True = lire aussi le corps de la fiche (sinon titre + description)
#   country             : lieu appliqué quand le texte n'en cite aucun
#   enabled             : False pour désactiver sans supprimer
# ------------------------------------------------------------
DIRECTORIES = [
    {
        "name": "TheCondia - communautés tech (Nigeria)",
        "mode": "article_list",
        "urls": ["https://thecondia.com/tech-communities-career-ng/"],
        "country": "Nigeria",
        "enabled": True,
    },
    {
        "name": "TheCondia - communautés de femmes en tech",
        "mode": "article_list",
        "urls": ["https://thecondia.com/tech-communities-for-girls-in-africa/"],
        "enabled": True,
    },
    {
        "name": "HackerNoon - 18 communautés de femmes en tech",
        "mode": "article_list",
        "urls": ["https://hackernoon.com/18-female-tech-communities-in-africa"],
        "enabled": True,
    },
    {
        "name": "Silicon Africa - communautés du Nigeria",
        "mode": "article_list",
        "urls": ["https://siliconafrica.org/latest-tech-communities-in-nigeria/"],
        "country": "Nigeria",
        "enabled": True,
    },
    {
        "name": "All About Accra - communautés tech",
        "mode": "article_list",
        "urls": ["https://www.allaboutaccra.com/guides/tech-communities-in-accra"],
        "country": "Accra, Ghana",
        "enabled": True,
    },
    {
        "name": "Syntax and Script - communautés africaines",
        "mode": "article_list",
        "urls": ["https://syntaxandscript.com/thriving-tech-communities-for-african-tech-professionals/"],
        "enabled": True,
    },
    {
        "name": "Oscar Mini - meilleures communautés d'Afrique",
        "mode": "article_list",
        "urls": ["https://oscarmini.com/best-tech-communities-to-join-in-africa"],
        "enabled": True,
    },
    # Modèle d'annuaire à fiches : renseigne l'agenda et le motif des fiches.
    {
        "name": "Annuaire à compléter",
        "mode": "directory",
        "urls": [],
        "follow": None,   # ex. r"^/communities/[a-z0-9-]+$"
        "enabled": False,
    },
]

# Médias (flux RSS) : désactivés par défaut, car un article n'est pas une
# communauté. À activer pour repérer des lancements annoncés dans la presse.
FEEDS = [
    {"name": "TechCabal", "url": "https://techcabal.com/feed/", "enabled": False},
    {"name": "Techpoint Africa", "url": "https://techpoint.africa/feed/", "enabled": False},
]

COMMUNITY_WORDS = [
    "community", "communities", "communauté", "communautés", "meetup", "chapter",
    "user group", "developers", "developer", "développeurs", "devs", "dev",
    "network", "réseau", "collective", "girls", "women", "club", "guild",
    "bootcamp", "hackers", "slack", "discord", "whatsapp", "mentorship",
    "open source", "gdg", "devfest",
]

TECH_WORDS = [
    "technology", "tech", "startup", "software", "ai", "artificial intelligence",
    "machine learning", "fintech", "blockchain", "web3", "cloud", "data",
    "cybersecurity", "mobile", "open source", "product", "design", "ux",
    "javascript", "python", "android", "devops", "programming", "coding",
]

COMMUNITY_PATTERNS = core.make_patterns(COMMUNITY_WORDS)
TECH_PATTERNS = core.make_patterns(TECH_WORDS)

# Titres de sections qui ne sont jamais une communauté
IGNORED_HEADINGS = re.compile(
    r"conclusion|introduction|related|share|comment|faq|final|subscribe|newsletter|"
    r"about the author|read more|join the|why |how to|tips|what is|summary|"
    r"table of contents|you may also|leave a|recommended|more on",
    re.I,
)

INITIALS_STOPWORDS = {
    "africa", "african", "afrique", "community", "communauté", "developers",
    "developer", "devs", "group", "groups", "the", "of", "for", "and", "&",
    "official", "org", "hub", "in", "tech",
}

MEMBERS_RE = re.compile(
    r"(\d[\d,.\s]{1,9}\d|\d)\s*\+?\s*(?:members|membres|developers|développeurs|devs|followers)",
    re.I,
)


# ============================================================
# CONSTRUCTION DE L'ENREGISTREMENT
# ============================================================

def make_initials(name):
    tokens = re.findall(r"[A-Za-z0-9À-ÿ]+", name or "")
    kept = [t for t in tokens if t.lower() not in INITIALS_STOPWORDS] or tokens

    if not kept:
        return ""

    # Un sigle déjà en majuscules ("GDG", "JS") est conservé tel quel
    for token in kept:
        if token.isupper() and 2 <= len(token) <= 4:
            return token

    return "".join(t[0] for t in kept[:3]).upper()


def members_count(text):
    match = MEMBERS_RE.search(text or "")
    if not match:
        return None

    digits = re.sub(r"[^\d]", "", match.group(1))
    return f"{int(digits):,} membres" if digits else None


def build_community(name, description, link, image, location, text, source, origin):
    now = core.now_iso()
    q = (
        core.find_terms(text, COMMUNITY_PATTERNS)
        + core.africa_terms(text)
        + core.find_terms(text, TECH_PATTERNS)
    )

    return {
        "name": name,
        "slug": core.slugify(name) or core.slugify(link),
        "initials": make_initials(name),
        "location": location,
        "is_active": False,
        "members_count": members_count(text),
        "description": description[:500],
        "next_meetup_title": None,
        "next_meetup_date": None,
        "action_label": "Rejoindre",
        "link": link,
        "img": [image] if image else [],
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "q": q or [source["name"]],
        "_source_": source["name"],
        "_origin_": origin,
    }


def resolve_location(text, source):
    return core.find_place(text) or source.get("country") or "Afrique"


# ------------------------------------------------------------
# 1) Section d'un article-liste
# ------------------------------------------------------------

def record_from_section(section, page_url, source):
    heading = re.sub(r"^\s*(?:#?\d+[.):\-]?|[•\-–])\s*", "", section["heading"]).strip(" :–-")

    if not (2 <= len(heading) <= 70) or IGNORED_HEADINGS.search(heading):
        return None

    description = section["text"]
    if len(description) < 40 or not section["links"]:
        return None

    link = section["links"][0][0]
    image = next((i for i in section["images"] if i.startswith("http")), None)
    text = f"{heading} {description}"

    return build_community(
        heading, description, link, image,
        resolve_location(text, source), text, source, "section",
    )


# ------------------------------------------------------------
# 2) Annuaire : JSON-LD puis meta de la fiche
# ------------------------------------------------------------

LD_TYPES = {"Organization", "NGO", "EducationalOrganization", "Corporation", "Project"}


def record_from_ld(node, page_url, source):
    name = core.as_text(node.get("name"))
    description = core.as_text(node.get("description"))

    if not name or len(description) < 20:
        return None

    link = core.normalize_url(node.get("url") or page_url)
    city, country = core.jsonld_place(node)
    text = f"{name} {description}"

    location = (
        ", ".join(x for x in (city, country) if x)
        or resolve_location(text, source)
    )

    return build_community(
        name, description, link, core.jsonld_image(node),
        location, text, source, "ld",
    )


def record_from_page(page, page_url, source):
    name = core.page_title(page)
    description = core.meta_content(page, "og:description") or core.meta_content(page, "description") or ""

    if not name or len(description) < 20:
        return None

    image = core.meta_content(page, "og:image")
    body = core.page_text(page)[:3000] if source.get("use_body") else ""
    text = f"{name} {description} {body}"

    return build_community(
        name, description, page_url, image if image and image.startswith("http") else None,
        resolve_location(text, source), text, source, "page",
    )


# ------------------------------------------------------------
# 3) Article de presse (flux RSS)
# ------------------------------------------------------------

def record_from_entry(entry, source_name):
    title = core.clean_html(entry.get("title", ""))
    link = core.normalize_url(entry.get("link", ""))
    description = core.clean_html(entry.get("summary", ""))

    if not title or not link:
        return None

    text = f"{title} {description}"
    source = {"name": source_name}

    return build_community(
        title, description, link, None,
        resolve_location(text, source), text, source, "article",
    )


# ============================================================
# PERTINENCE
# ============================================================

def is_relevant(record, source):
    text = f"{record['name']} {record['description']}"

    if not record["name"] or not record["link"]:
        return False
    if len(record["description"]) < 20:
        return False

    # Une communauté doit ressembler à une communauté (sauf dans un article
    # dont c'est justement le sujet)...
    if record["_origin_"] != "section" and not core.find_terms(text, COMMUNITY_PATTERNS):
        return False

    # ... et être africaine : lieu connu, ou source dédiée à l'Afrique
    return bool(core.africa_terms(f"{text} {record['location']}")) or bool(source.get("country"))


def dedup_key(record):
    return core.slugify(record.get("name", ""))


PROFILE = {
    "title": "Community collector",
    "output": OUTPUT_PATH,
    "root_key": "communities",
    "directories": DIRECTORIES,
    "feeds": FEEDS,
    "ld_types": LD_TYPES,
    "record_from_ld": record_from_ld,
    "record_from_page": record_from_page,
    "record_from_section": record_from_section,
    "record_from_entry": record_from_entry,
    "is_relevant": is_relevant,
    "dedup_key": dedup_key,
}


def run_collector():
    core.run(PROFILE)


if __name__ == "__main__":
    run_collector()
