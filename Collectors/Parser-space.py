import re
from pathlib import Path

import collector_core as core


# ============================================================
# CONFIGURATION
# ============================================================

# Racine du repo = dossier parent de Collectors/
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = BASE_DIR / "Datas" / "Spaces.json"
ROOT_KEY = "spaceships"          # clé racine du JSON (schéma du site)

core.REQUEST_DELAY = 1.0
core.MAX_PAGES_PER_SOURCE = 150  # fiches suivies par source

# ------------------------------------------------------------
# Sources
#   mode "directory"    : annuaire à fiches ; JSON-LD schema.org en priorité,
#                         sinon balises meta de la fiche
#   mode "article_list" : article présentant une liste d'espaces
#   follow       : regex sur le chemin des liens vers les fiches
#   default_type : type retenu quand le texte n'en précise aucun
#   visit_text   : texte du bouton qui mène au site officiel de l'espace
#   use_body     : True = lire aussi le corps de la fiche (adresse, commodités, prix)
#   urls         : pages d'agenda ; {"url_template": "...{n}", "max_pages": N}
#                  pour la pagination, "country" pour fixer le pays
#   enabled      : False pour désactiver sans supprimer
# ------------------------------------------------------------
DIRECTORIES = [
    {
        "name": "AfriLabs",
        "mode": "directory",
        "urls": [{"url_template": "https://www.afrilabs.com/members/page/{n}/", "max_pages": 25}],
        "follow": r"^/hub/[a-z0-9-]+/?$",
        "default_type": "techhub",
        "enabled": True,
    },
    {
        "name": "Freaking Nomads",
        "mode": "directory",
        "urls": [
            {"url_template": f"https://freakingnomads.com/workspaces/coworking/{slug}?page={{n}}",
             "max_pages": 8, "country": country}
            for slug, country in [
                ("kenya", "Kenya"), ("nigeria", "Nigeria"), ("ghana", "Ghana"),
                ("south-africa", "Afrique du Sud"), ("senegal", "Sénégal"),
                ("morocco", "Maroc"), ("egypt", "Égypte"), ("rwanda", "Rwanda"),
                ("tanzania", "Tanzanie"), ("uganda", "Ouganda"),
                ("ivory-coast", "Côte d'Ivoire"), ("cameroon", "Cameroun"),
            ]
        ],
        "follow": r"^/workspace/[a-z0-9-]+$",
        "default_type": "coworking",
        "visit_text": r"visit website|site web|visiter",
        "use_body": True,     # adresse, commodités et tarifs figurent dans la fiche
        "enabled": True,
    },
    {
        "name": "Techpoint - coworkings de Lagos",
        "mode": "article_list",
        "urls": ["https://techpoint.africa/general/co-working-spaces-lagos-nigeria/"],
        "country": "Lagos, Nigeria",
        "default_type": "coworking",
        "enabled": True,
    },
    {
        "name": "WeeTracker - coworkings de Nairobi",
        "mode": "article_list",
        "urls": ["https://weetracker.com/2018/05/18/an-exhaustive-list-of-co-working-spaces-in-nairobi/"],
        "country": "Nairobi, Kenya",
        "default_type": "coworking",
        "enabled": True,
    },
]

# Médias (flux RSS) : désactivés par défaut, un article n'est pas un espace.
FEEDS = [
    {"name": "TechCabal", "url": "https://techcabal.com/feed/", "enabled": False},
    {"name": "Techpoint Africa", "url": "https://techpoint.africa/feed/", "enabled": False},
]

# Types d'espaces : clé -> (libellé du badge, libellé du bouton, mots-clés)
TYPES = {
    "fablab": ("Fablab", "Visiter", ["fablab", "fab lab", "makerspace", "maker space", "hackerspace"]),
    "incubateur": ("Incubateur", "Visiter", ["incubator", "incubateur", "accelerator", "accélérateur", "startup studio"]),
    "coworking": ("Coworking", "Réserver", ["coworking", "co-working", "workspace", "work space", "shared office", "bureaux partagés", "espace de travail"]),
    "techhub": ("Tech hub", "Visiter", ["tech hub", "innovation hub", "digital center", "digital hub", "innovation center", "hub", "labs"]),
}
TYPE_PATTERNS = {key: core.make_patterns(v[2]) for key, v in TYPES.items()}
ALL_SPACE_PATTERNS = [p for patterns in TYPE_PATTERNS.values() for p in patterns]

# Commodités mises en avant dans le badge (la première trouvée l'emporte)
PERKS = [
    ("Générateur", ["generator", "backup power", "power backup", "générateur", "24/7 power", "inverter"]),
    ("Fibre", ["fibre", "fiber", "fibre optique"]),
    ("Wi-Fi", ["wi-fi", "wifi", "high-speed internet", "internet"]),
]
PERK_PATTERNS = [(label, core.make_patterns(words)) for label, words in PERKS]

TECH_WORDS = [
    "tech", "startup", "innovation", "digital", "entrepreneur", "founders",
    "developers", "software", "ai", "fintech", "design", "creative",
]
TECH_PATTERNS = core.make_patterns(TECH_WORDS)

IGNORED_HEADINGS = re.compile(
    r"conclusion|introduction|related|share|comment|faq|final|subscribe|newsletter|"
    r"about the author|read more|why |how to|tips|what is|summary|table of contents|"
    r"you may also|leave a|recommended|more on|benefits",
    re.I,
)

PRICE_RE = re.compile(
    r"(?:from\s+|à partir de\s+)?(?:[$€£]\s?\d[\d,.\s]*|\d[\d,.\s]*\s?(?:usd|eur|kes|ngn|xof|fcfa|ghs|zar|mad|egp|rwf|tzs|ugx))"
    r"(?:\s?/\s?(?:day|month|hour|week|jour|mois|heure|semaine|j|h|d))?",
    re.I,
)


# ============================================================
# CONSTRUCTION DE L'ENREGISTREMENT
# ============================================================

def detect_type(text, default):
    for key, patterns in TYPE_PATTERNS.items():
        if core.find_terms(text, patterns):
            return key
    return default


def pick_badge(text, type_label):
    for label, patterns in PERK_PATTERNS:
        if core.find_terms(text, patterns):
            return label
    return type_label


def pick_price(text):
    if re.search(r"\b(free of charge|gratuit|free to use)\b", text, re.I):
        return "Gratuit"

    match = PRICE_RE.search(text)
    return re.sub(r"\s+", " ", match.group(0)).strip()[:40] if match else None


def build_space(name, description, link, image, location, text, source, origin):
    now = core.now_iso()
    default = source.get("default_type", "coworking")
    space_type = detect_type(f"{name} {description}", default)
    type_label, action_label = TYPES[space_type][:2]

    q = (
        core.find_terms(text, ALL_SPACE_PATTERNS)
        + core.africa_terms(text)
        + core.find_terms(text, TECH_PATTERNS)
    )

    if not description:
        description = f"{type_label} à {location}."

    return {
        "name": name,
        "slug": core.slugify(name) or core.slugify(link),
        "is_active": False,
        "location": location,
        "badge": pick_badge(text, type_label),
        "description": description[:500],
        "price": pick_price(text),
        "action_label": action_label,
        "img": [image] if image else [],
        "link": link,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "q": q or [source["name"]],
        "_type_": space_type,
        "_source_": source["name"],
        "_origin_": origin,
    }


def resolve_location(text, source):
    """Ville, Pays : d'abord 'Ville, <pays de la source>' dans le texte, puis les lieux connus."""
    country = source.get("country")

    if country and "," not in country:
        match = re.search(rf"([A-Z][A-Za-zÀ-ÿ'.\- ]{{2,30}}),\s*{re.escape(country)}", text)
        if match:
            return f"{match.group(1).strip()}, {country}"

    place = core.find_place(text)
    if place:
        return place

    return country or "Afrique"


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

    return build_space(
        heading, description, link, image,
        resolve_location(text, source), text, source, "section",
    )


# ------------------------------------------------------------
# 2) Annuaire : JSON-LD puis meta de la fiche
# ------------------------------------------------------------

LD_TYPES = {
    "LocalBusiness", "Organization", "ProfessionalService", "Place",
    "CoworkingSpace", "EducationalOrganization", "NGO",
}


def record_from_ld(node, page_url, source):
    name = core.as_text(node.get("name"))
    description = core.as_text(node.get("description"))

    if not name:
        return None

    link = core.normalize_url(node.get("url") or page_url)
    city, country = core.jsonld_place(node)
    text = f"{name} {description}"

    location = (
        ", ".join(x for x in (city, country) if x)
        or core.find_place(text) or source.get("country") or "Afrique"
    )

    return build_space(
        name, description, link, core.jsonld_image(node),
        location, text, source, "ld",
    )


def record_from_page(page, page_url, source):
    name = core.page_title(page)
    description = core.meta_content(page, "og:description") or core.meta_content(page, "description") or ""

    if not name:
        return None

    image = core.meta_content(page, "og:image")
    # Le corps de la page (adresse, commodités, tarifs) n'est lu que si la source
    # le demande : sinon on y trouverait le menu et le pied de page du site.
    body = core.page_text(page)[:4000] if source.get("use_body") else ""
    text = f"{name} {description} {body}"

    # Le site officiel de l'espace vaut mieux que la fiche de l'annuaire
    link = core.visit_link(page, page_url, source.get("visit_text")) or page_url

    return build_space(
        name, description, link, image if image and image.startswith("http") else None,
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

    return build_space(
        title, description, link, None,
        resolve_location(text, source), text, source, "article",
    )


# ============================================================
# PERTINENCE
# ============================================================

def is_relevant(record, source):
    if not record["name"] or not record["link"]:
        return False

    text = f"{record['name']} {record['description']}"

    # Une source consacrée aux espaces (default_type) suffit ; sinon le texte doit l'indiquer
    if record["_origin_"] not in ("page", "section") or not source.get("default_type"):
        if not core.find_terms(text, ALL_SPACE_PATTERNS):
            return False

    return bool(core.africa_terms(f"{text} {record['location']}")) or bool(source.get("country")) \
        or source.get("name") == "AfriLabs"


def dedup_key(record):
    country = (record.get("location") or "").split(",")[-1].strip()
    return f"{core.slugify(record.get('name', ''))}|{core.slugify(country)}"


PROFILE = {
    "title": "Space collector",
    "output": OUTPUT_PATH,
    "root_key": ROOT_KEY,
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
