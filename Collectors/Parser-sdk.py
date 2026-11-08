import base64
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SEARCH_URL = "https://api.github.com/search/repositories"
USERS_SEARCH_URL = "https://api.github.com/search/users"
GITHUB_API = "https://api.github.com"

MAX_ENRICHED_REPOS = 100   # None = enrichir tous les dépôts trouvés
USERS_PER_LOCATION = 10    # devs les plus suivis retenus par lieu
REPOS_PER_USER = 30        # dépôts récents examinés par dev

# Lieux cherchés dans le champ "location" des profils GitHub (texte libre).
LOCATIONS = [
    "africa", "ghana", "kenya", "nigeria", "senegal", "ivory coast",
    "côte d'ivoire", "cameroon", "morocco", "egypt", "rwanda", "mozambique",
]

# Racine du repo = dossier parent de Collectors/
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = BASE_DIR / "Datas" / "SDKs.json"

SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
})
token = os.getenv("GITHUB_TOKEN")
if token:
    SESSION.headers["Authorization"] = f"Bearer {token}"


def api_get(url, params=None):
    """Requête GitHub ; renvoie le JSON ou None en cas d'échec."""
    try:
        response = SESSION.get(url, params=params, timeout=30)

        if response.status_code == 404:  # pas de README / release : normal
            return None
        if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
            print(f"⚠️ Quota GitHub atteint (reset Unix : {response.headers.get('X-RateLimit-Reset')})")
            return None

        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"⚠️ Échec sur {url}: {exc}")
        return None


def decode_readme(readme_data):
    if not readme_data or not readme_data.get("content"):
        return None
    try:
        return base64.b64decode(readme_data["content"]).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return None


def find_documentation_url(readme_text, homepage):
    """Cherche un lien de doc dans le README, sinon utilise le homepage."""
    patterns = [
        r'\[[^\]]*(?:documentation|docs|doc)[^\]]*\]\((https?://[^)\s]+)\)',
        r'https?://[^\s<>)"]*(?:docs|documentation)[^\s<>)"]*',
    ]
    for pattern in patterns:
        match = re.search(pattern, readme_text or "", flags=re.IGNORECASE)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
    return homepage or None


def build_record(repo, query, crawled_at):
    owner = repo.get("owner") or {}
    lic = repo.get("license") or {}
    licence = lic.get("spdx_id") or lic.get("name")
    homepage = repo.get("homepage") or None
    return {
        "id": repo["id"],
        "title": repo.get("name"),
        "description": repo.get("description"),
        "author_name": owner.get("login"),
        "_source_url_": repo.get("html_url"),
        "stars": repo.get("stargazers_count", 0),
        "primary_language": repo.get("language"),
        "documentation_url": homepage,
        "version": None,
        "reviews_count": None,
        "average_rating": None,
        "_location_": None,
        "_forks_": repo.get("forks_count", 0),
        "_readme_length_": None,
        "_website_": homepage,
        "_licence_": licence,
        "_depreciated_": bool(repo.get("archived", False)),
        "created_at": repo.get("created_at"),
        "updated_at": repo.get("updated_at"),
        "_last_activity_": repo.get("pushed_at"),
        "topics": repo.get("topics", []),
        "full_name": repo.get("full_name"),
        "default_branch": repo.get("default_branch"),
        "open_issues_count": repo.get("open_issues_count", 0),
        "owner_type": owner.get("type"),
        "author_email": None,
        "author_profile_url": owner.get("html_url"),
        "author_avatar_url": owner.get("avatar_url"),
        "readme_url": None,
        "release_url": None,
        "_crawled_at_": crawled_at,
        "q?": query,
    }


def enrich(record, owner_cache):
    """Ajoute README, dernière release et infos publiques du propriétaire."""
    full_name = record["full_name"]
    login = record["author_name"]

    if login and login not in owner_cache:
        owner_cache[login] = api_get(f"{GITHUB_API}/users/{login}") or {}
    owner = owner_cache.get(login, {})

    readme = api_get(f"{GITHUB_API}/repos/{full_name}/readme")
    readme_text = decode_readme(readme)
    release = api_get(f"{GITHUB_API}/repos/{full_name}/releases/latest")

    record.update({
        "documentation_url": find_documentation_url(readme_text, record["_website_"]),
        "version": (release or {}).get("tag_name"),
        "_location_": owner.get("location"),
        "_readme_length_": len(readme_text) if readme_text is not None else None,
        "readme_url": (readme or {}).get("html_url"),
        "release_url": (release or {}).get("html_url"),
        "author_email": owner.get("email"),
    })


def collect_by_keywords(queries, records, crawled_at):
    for query in queries:
        print(f"\n🔍 Recherche : {query}")
        data = api_get(SEARCH_URL, params={
            "q": query, "sort": "updated", "order": "desc", "per_page": 100,
        })
        if data is None:
            continue

        print(f"📊 Résultats annoncés : {data.get('total_count', 0)}")
        for repo in data.get("items", []):  # l'API renvoie "items"
            if repo["id"] not in records:
                records[repo["id"]] = build_record(repo, query, crawled_at)

        time.sleep(2.5)  # l'API de recherche est limitée à ~30 req/min


def collect_by_location(locations, since, records, crawled_at):
    """Cherche les devs par lieu (search/users), puis liste leurs dépôts récents."""
    for place in locations:
        query = f'location:"{place}" repos:>0'
        print(f"\n📍 Devs localisés : {place}")
        data = api_get(USERS_SEARCH_URL, params={
            "q": query, "sort": "followers", "order": "desc",
            "per_page": USERS_PER_LOCATION,
        })
        time.sleep(2.5)
        if data is None:
            continue

        for user in data.get("items", []):
            repos = api_get(f"{GITHUB_API}/users/{user['login']}/repos", params={
                "type": "owner", "sort": "pushed", "direction": "desc",
                "per_page": REPOS_PER_USER,
            })
            time.sleep(0.15)

            for repo in repos or []:
                if repo.get("fork") or (repo.get("pushed_at") or "") < since:
                    continue
                if repo["id"] not in records:
                    records[repo["id"]] = build_record(repo, f"location:{place} (users)", crawled_at)


def run_collector():
    print("🤖 Le robot GitHub démarre...")

    since = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")
    queries = [
        f"fintech-africa pushed:>={since}",
        f"africa-api african-api open-source-africa pushed:>={since}",
        f"africa-tech african-tech africa-sdk pushed:>={since}",
        f"africa package pushed:>={since}",
        f"africa in:readme sdk pushed:>={since}",
        "open-source-africa in:readme package pushed:>=2024-01-01",
        "africa-tech african-tech africa-sdk in:readme package pushed:>=2024-01-01",
    ]

    records = {}
    owner_cache = {}
    crawled_at = datetime.now(timezone.utc).isoformat()

    collect_by_keywords(queries, records, crawled_at)
    collect_by_location(LOCATIONS, since, records, crawled_at)

    items = sorted(records.values(), key=lambda r: r["pushed_at"] or "", reverse=True)
    limit = len(items) if MAX_ENRICHED_REPOS is None else min(len(items), MAX_ENRICHED_REPOS)

    print(f"\n🔧 Enrichissement de {limit} dépôts sur {len(items)}...")
    for index, record in enumerate(items[:limit], start=1):
        print(f"   [{index}/{limit}] {record['full_name']}")
        enrich(record, owner_cache)
        time.sleep(0.15)

    output = {
        "source": "github-actions-bot",
        "last_update": datetime.now(timezone.utc).isoformat(),
        "queries_used": queries + [f"location:{p} (users)" for p in LOCATIONS],
        "total_found": len(items),
        "libraries": items,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)

    print(f"\n✅ {len(items)} dépôts uniques, {limit} enrichis → {OUTPUT_PATH}")


if __name__ == "__main__":
    run_collector()
