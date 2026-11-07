import base64
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests

SEARCH_URL = "https://api.github.com/search/repositories"
USERS_SEARCH_URL = "https://api.github.com/search/users"
USER_URL = "https://api.github.com/users/{login}"
USER_REPOS_URL = "https://api.github.com/users/{login}/repos"
REPO_URL = "https://api.github.com/repos/{full_name}"

MAX_ITEMS = 250            # nombre max de dépôts conservés (et enrichis)
USERS_PER_LOCATION = 6     # devs les plus suivis retenus par lieu
REPOS_PER_USER = 30        # dépôts récents examinés par dev
SINCE = "2025-01-01"       # aucun dépôt sans push depuis cette date
DEFAULT_STATUS = "pending"  # "accepted" si tu veux qu'ils s'affichent sans validation
RATE_LIMIT_RESERVE = 25    # sous ce quota restant, on arrête les appels coûteux

# Lieu cherché dans les profils GitHub (texte libre, sans correction d'accents :
# on met donc les variantes) -> pays utilisé si le profil ne le précise pas.
LOCATIONS = {
    # Côte d'Ivoire, Rwanda, Maroc
    "ivory coast": "Côte d’Ivoire",
    "côte d'ivoire": "Côte d’Ivoire",
    "abidjan": "Côte d’Ivoire",
    "rwanda": "Rwanda",
    "morocco": "Maroc",
    "maroc": "Maroc",
    # Principaux pôles tech du continent
    "nigeria": "Nigeria",
    "lagos": "Nigeria",
    "kenya": "Kenya",
    "nairobi": "Kenya",
    "south africa": "Afrique du Sud",
    "ghana": "Ghana",
    "egypt": "Égypte",
    "cairo": "Égypte",
    "senegal": "Sénégal",
    "sénégal": "Sénégal",
    "dakar": "Sénégal",
    "tunisia": "Tunisie",
    "tunisie": "Tunisie",
    "cameroon": "Cameroun",
    "cameroun": "Cameroun",
    "uganda": "Ouganda",
    "tanzania": "Tanzanie",
    "ethiopia": "Éthiopie",
    "algeria": "Algérie",
}

# Chaque requête est complétée par "pushed:>=SINCE archived:false" (forks exclus par défaut).
# Les requêtes "topic:" sont triées par étoiles, les requêtes texte par pertinence.
KEYWORD_QUERIES = [
    # afritech
    "topic:afritech",
    "afritech in:name,description,topics,readme",
    # Afrique, généralistes
    "africa package",
    "africa african open-source-africa in:readme package",
    "africa in:readme package",
    "africa sdk in:name,description,topics",
    "african api in:name,description,topics",
    "africa validator in:name,description,topics",
    # Topics croisés
    "topic:africa topic:sdk",
    "topic:africa topic:api",
    "topic:africa topic:library",
    "topic:africa topic:fintech",
    "topic:africa topic:ai",
    "topic:africa topic:gis",
    "topic:african-languages",
    # Paiement mobile et télécoms
    "mobile money africa in:name,description,topics,readme",
    "topic:mpesa",
    "topic:daraja",
    "topic:ussd",
    "topic:flutterwave",
    "topic:paystack",
    "topic:mtn-momo",
    "topic:orange-money",
]

# Racine du repo = dossier parent de Collectors/
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = BASE_DIR / "Datas" / "initial_librairies.json"

SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
})
token = os.getenv("GITHUB_TOKEN")
if token:
    SESSION.headers["Authorization"] = f"Bearer {token}"


# --------------------------------------------------------------------------
# Accès API
# --------------------------------------------------------------------------
RATE = {"core_remaining": None}  # quota restant de l'API "core" (hors recherche)


def budget_ok():
    remaining = RATE["core_remaining"]
    return remaining is None or remaining > RATE_LIMIT_RESERVE


def api_get(url, params=None):
    """Requête GitHub ; renvoie le JSON ou None en cas d'échec."""
    for attempt in (1, 2):
        try:
            response = SESSION.get(url, params=params, timeout=30)
        except requests.exceptions.RequestException as exc:
            print(f"❌ Erreur réseau : {exc}")
            return None

        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None and response.headers.get("X-RateLimit-Resource") == "core":
            RATE["core_remaining"] = int(remaining)

        if response.status_code == 404:  # pas de README / release : normal
            return None
        if response.status_code in (403, 429):
            if remaining == "0":
                print(f"⚠️ Quota GitHub atteint (reset Unix : {response.headers.get('X-RateLimit-Reset')})")
                return None
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit() and attempt == 1:  # limite secondaire : on patiente, une fois
                print(f"⏳ Limite secondaire GitHub : pause de {retry_after}s")
                time.sleep(min(int(retry_after), 60))
                continue

        try:
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            print(f"❌ Erreur HTTP : {exc}")
        except ValueError as exc:
            print(f"❌ Erreur JSON : {exc}")
        return None
    return None


# --------------------------------------------------------------------------
# Déduction des champs (heuristiques ; on laisse vide plutôt que d'inventer)
# --------------------------------------------------------------------------
def slugify(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


COUNTRY_PATTERNS = [
    ("Sénégal", r"senegal|sénégal|dakar"),
    ("Côte d’Ivoire", r"ivory coast|c[oô]te d.ivoire|abidjan"),
    ("Ghana", r"ghana|accra"),
    ("Nigeria", r"nigeria|lagos|abuja"),
    ("Kenya", r"kenya|nairobi"),
    ("Maroc", r"morocco|maroc|casablanca|rabat|marrakech"),
    ("Égypte", r"egypt|[ée]gypte|cairo|le caire"),
    ("Rwanda", r"rwanda|kigali"),
    ("Cameroun", r"cameroon|cameroun|douala|yaound[ée]"),
    ("Tunisie", r"tunisia|tunisie|tunis"),
    ("Mali", r"\bmali\b|bamako"),
    ("Guinée", r"\bguin[ée]e?\b|conakry"),
    ("Mozambique", r"mozambique|maputo"),
    ("Afrique du Sud", r"south africa|afrique du sud|johannesburg|cape town"),
    ("Ouganda", r"uganda|ouganda|kampala"),
    ("Tanzanie", r"tanzania|tanzanie|dar es salaam"),
    ("Éthiopie", r"ethiopia|[ée]thiopie|addis"),
    ("Bénin", r"\bbenin\b|\bb[ée]nin\b|cotonou"),
    ("Togo", r"\btogo\b|lom[ée]"),
    ("Burkina Faso", r"burkina|ouagadougou"),
]


def detect_country(*texts):
    for text in texts:
        if not text:
            continue
        for country, pattern in COUNTRY_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return country
    return ""


FRAMEWORKS = {
    "fastapi": "FastAPI", "django": "Django", "flask": "Flask", "express": "Express",
    "nestjs": "NestJS", "nodejs": "Node.js", "node-js": "Node.js", "laravel": "Laravel",
    "symfony": "Symfony", "react": "React", "nextjs": "Next.js", "vue": "Vue.js",
    "vuejs": "Vue.js", "spring-boot": "Spring Boot", "gin": "Gin", "fiber": "Fiber",
    "flutter": "Flutter", "pytorch": "PyTorch", "tensorflow": "TensorFlow",
    "transformers": "Transformers", "android": "Android", "jetpack-compose": "Jetpack Compose",
}


def detect_frameworks(topics):
    found = []
    for topic in topics or []:
        name = FRAMEWORKS.get(topic.lower())
        if name and name not in found:
            found.append(name)
    return ",".join(found)


def detect_type(repo):
    ident = " ".join([repo.get("name") or ""] + (repo.get("topics") or [])).lower()
    if "snippet" in ident or "gist" in ident:
        return "snippet"
    if "sdk" in ident:
        return "sdk"
    if re.search(r"\bapi\b", ident.replace("-", " ").replace("_", " ")):
        return "api"
    return "sdk"


CATEGORY_RULES = [
    ("ussd", r"\bussd\b"),
    ("fintech", r"payment|paiement|mobile.?money|\bmomo\b|m-?pesa|\bwave\b|orange.?money|paystack|flutterwave|fintech|\bbank"),
    ("ai", r"\bai\b|\bnlp\b|machine.?learning|\bllm\b|deep.?learning|\bml\b"),
    ("gis", r"\bgis\b|\bgeo|\bmaps?\b|\bgps\b"),
    ("validation", r"validat"),
]


def detect_category(repo):
    text = " ".join(
        [repo.get("name") or "", repo.get("description") or ""] + (repo.get("topics") or [])
    ).lower().replace("_", " ")
    for category, pattern in CATEGORY_RULES:
        if re.search(pattern, text):
            return category
    return "utility"


def decode_readme(readme_data):
    if not readme_data or not readme_data.get("content"):
        return ""
    try:
        return base64.b64decode(readme_data["content"]).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def find_documentation_url(readme_text, homepage):
    patterns = [
        r'\[[^\]]*(?:documentation|docs|doc)[^\]]*\]\((https?://[^)\s]+)\)',
        r'https?://[^\s<>)"]*(?:docs|documentation)[^\s<>)"]*',
    ]
    for pattern in patterns:
        match = re.search(pattern, readme_text, flags=re.IGNORECASE)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
    return homepage or ""


SHELL_LANGS = {"", "bash", "sh", "shell", "console", "zsh", "powershell", "cmd", "text", "txt"}


def find_code_snippet(readme_text):
    """Premières lignes du premier bloc de code qui n'est pas une commande shell."""
    for lang, body in re.findall(r"```([\w+-]*)[^\n]*\n(.*?)```", readme_text, flags=re.DOTALL):
        if lang.lower() in SHELL_LANGS:
            continue
        lines = [line.rstrip() for line in body.splitlines() if line.strip()]
        if lines:
            return "\n".join(lines[:3])[:200]
    return ""


INSTALL_RE = re.compile(
    r"\b(pip3? install|npm (?:install|i)|yarn add|pnpm add|go get|composer require|cargo add|gem install)"
    r"\s+((?:-{1,2}[\w-]+\s+)*)([@\w./:\-\[\]]+)"
)


def find_install_command(readme_text, repo_name):
    """Commande d'installation lue dans le README (jamais devinée)."""
    candidates = []
    for cmd, _options, arg in INSTALL_RE.findall(readme_text):
        if arg in {".", ".."} or arg.endswith(".txt") or arg.startswith("-"):
            continue
        cmd = {"npm i": "npm install", "pip3 install": "pip install"}.get(cmd, cmd)
        candidates.append(f"{cmd} {arg}")
    for candidate in candidates:
        if repo_name.lower() in candidate.lower():
            return candidate
    return candidates[0] if candidates else ""


# --------------------------------------------------------------------------
# Construction d'un item (même structure que "libraries" de initial_data.json)
# --------------------------------------------------------------------------
def build_item(repo, owner, readme_text, release, country_hint):
    license_data = repo.get("license") or {}
    spdx = license_data.get("spdx_id") or ""
    if spdx == "NOASSERTION":
        spdx = ""

    author_login = (repo.get("owner") or {}).get("login") or ""
    author_name = owner.get("name") or author_login
    homepage = repo.get("homepage") or ""

    return {
        "id": repo["id"],
        "title": repo.get("name") or "",
        "slug": slugify(repo.get("full_name") or repo.get("name")),
        "type": detect_type(repo),
        "is_active": not repo.get("archived", False),
        "category": detect_category(repo),
        "primary_language": repo.get("language") or "",
        "supported_frameworks": detect_frameworks(repo.get("topics")),
        "country": detect_country(owner.get("location")) or country_hint or "",
        "description": repo.get("description") or "",
        "code_snippet": find_code_snippet(readme_text),
        "documentation_url": find_documentation_url(readme_text, homepage) or repo.get("html_url") or "",
        "github_url": repo.get("html_url") or "",
        "package_manager_command": find_install_command(readme_text, repo.get("name") or ""),
        "version": (release or {}).get("tag_name") or "",
        "license": spdx,
        "stars": repo.get("stargazers_count", 0),
        "reviews_count": 0,
        "average_rating": 0.0,
        "author_name": author_name,
        "author_slug": slugify(author_login),
        "author_verified": 0,
        "status": DEFAULT_STATUS,
        "created_at": repo.get("created_at"),
        "updated_at": repo.get("updated_at"),
        "reviews": [],
    }


# --------------------------------------------------------------------------
# Collecte
# --------------------------------------------------------------------------
RELEVANT_RE = re.compile(r"\b(sdk|api|library|librairie|package|client|wrapper|toolkit|framework|plugin|module)\b", re.I)


def looks_relevant(repo):
    text = " ".join(
        [repo.get("name") or "", repo.get("description") or ""] + (repo.get("topics") or [])
    ).replace("-", " ").replace("_", " ")
    return bool(RELEVANT_RE.search(text))


def collect_by_keywords(candidates):
    for query in KEYWORD_QUERIES:
        full_query = f"{query} pushed:>={SINCE} archived:false"
        params = {"q": full_query, "order": "desc", "per_page": 100}
        if query.startswith("topic:"):
            params["sort"] = "stars"  # sinon : tri par pertinence (best match)

        print(f"\n🔍 Recherche : {full_query}")
        data = api_get(SEARCH_URL, params=params)
        time.sleep(2.5)  # l'API de recherche est limitée à ~30 req/min
        if data is None:
            continue

        print(f"📊 Résultats trouvés : {data.get('total_count', 0)}")
        for repo in data.get("items", []):
            if repo.get("id") and not repo.get("fork") and not repo.get("archived"):
                candidates.setdefault(repo["id"], (repo, ""))


def collect_by_location(candidates):
    """Cherche les devs par lieu (search/users), puis garde leurs dépôts récents pertinents."""
    seen_users = set()  # un dev trouvé via "nigeria" et "lagos" n'est listé qu'une fois
    for place, country in LOCATIONS.items():
        if not budget_ok():
            print("⚠️ Quota API presque épuisé : recherche par lieu interrompue.")
            return

        print(f"\n📍 Devs localisés : {place}")
        data = api_get(USERS_SEARCH_URL, params={
            "q": f'location:"{place}" repos:>0',
            "sort": "followers", "order": "desc", "per_page": USERS_PER_LOCATION,
        })
        time.sleep(2.5)
        if data is None:
            continue

        for user in data.get("items", []):
            if user["login"] in seen_users:
                continue
            seen_users.add(user["login"])

            repos = api_get(USER_REPOS_URL.format(login=user["login"]), params={
                "type": "owner", "sort": "pushed", "direction": "desc",
                "per_page": REPOS_PER_USER,
            })
            time.sleep(0.15)

            for repo in repos or []:
                if repo.get("fork") or repo.get("archived") or not repo.get("id"):
                    continue
                if (repo.get("pushed_at") or "") < SINCE or not looks_relevant(repo):
                    continue
                candidates.setdefault(repo["id"], (repo, country))


def run_collector():
    print("🤖 Le robot GitHub démarre...")

    candidates = {}   # repo_id -> (repo, country_hint)
    collect_by_keywords(candidates)
    collect_by_location(candidates)

    # On garde les MAX_ITEMS dépôts les plus étoilés (à égalité : le plus récent).
    # Les plus populaires sont enrichis en premier si le quota venait à manquer.
    ranked = sorted(
        candidates.values(),
        key=lambda pair: (pair[0].get("stargazers_count", 0), pair[0].get("pushed_at") or ""),
        reverse=True,
    )[:MAX_ITEMS]

    print(f"\n🔧 Enrichissement de {len(ranked)} dépôts sur {len(candidates)} candidats...")
    owner_cache = {}
    items = []
    skipped = 0
    for index, (repo, country_hint) in enumerate(ranked, start=1):
        full_name = repo.get("full_name")
        login = (repo.get("owner") or {}).get("login")
        print(f"   [{index}/{len(ranked)}] {full_name}")

        owner, readme_text, release = {}, "", None
        if budget_ok():
            if login and login not in owner_cache:
                owner_cache[login] = api_get(USER_URL.format(login=login)) or {}
            owner = owner_cache.get(login, {})
            readme_text = decode_readme(api_get(REPO_URL.format(full_name=full_name) + "/readme"))
            release = api_get(REPO_URL.format(full_name=full_name) + "/releases/latest")
            time.sleep(0.15)
        else:
            owner = owner_cache.get(login, {})
            skipped += 1

        items.append(build_item(repo, owner, readme_text, release, country_hint))

    if skipped:
        print(f"⚠️ Quota API presque épuisé : {skipped} dépôts ajoutés sans README ni release.")

    # Garde-fou : ne jamais écraser le fichier existant par un tableau vide
    # (quota atteint, panne réseau...). Le job échoue et rien n'est commité.
    if not items:
        print("❌ Aucun dépôt collecté : le fichier existant est conservé.")
        sys.exit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OUTPUT_PATH.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(items, file, ensure_ascii=False, indent=2)
        file.write("\n")
    tmp_path.replace(OUTPUT_PATH)  # écriture atomique

    print("\n" + "=" * 40)
    print(f"✅ Collecte terminée : {len(items)} dépôts → {OUTPUT_PATH}")
    print("=" * 40)


if __name__ == "__main__":
    run_collector()
