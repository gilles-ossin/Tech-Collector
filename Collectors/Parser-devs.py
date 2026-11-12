import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SEARCH_URL = "https://api.github.com/search/repositories"
USERS_SEARCH_URL = "https://api.github.com/search/users"
USER_REPOS_URL = "https://api.github.com/users/{login}/repos"

USERS_PER_LOCATION = 10   # devs les plus suivis retenus par lieu
REPOS_PER_USER = 30       # dépôts récents examinés par dev
LOCATION_SINCE = "2025-01-01"  # on ignore les dépôts sans push depuis cette date

# Lieux cherchés dans le champ "location" des profils GitHub (texte libre).
LOCATIONS = ["ivory coast", "côte d'ivoire", "rwanda", "morocco"]

# Fichier écrit dans le dossier d'où le script est lancé (racine du repo dans le workflow).
OUTPUT_PATH = Path("Datas/Devs.json")

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

        if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
            print(f"⚠️ Quota GitHub atteint (reset Unix : {response.headers.get('X-RateLimit-Reset')})")
            return None

        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as exc:
        print(f"❌ Erreur HTTP : {exc}")
    except requests.exceptions.RequestException as exc:
        print(f"❌ Erreur réseau : {exc}")
    except ValueError as exc:
        print(f"❌ Erreur JSON : {exc}")
    return None


def build_record(repo, query):
    return {
        "title": repo.get("name"),
        "description": repo.get("description"),
        "type": "SDK / Package",
        "github_url": repo.get("html_url"),
        "updated_at": repo.get("updated_at"),
        "pushed_at": repo.get("pushed_at"),
        "stars": repo.get("stargazers_count"),
        "author": (repo.get("owner") or {}).get("login"),
        "q?": query,
    }


def collect_by_keywords(queries, repositories):
    for query in queries:
        print(f"\n🔍 Recherche : {query}")
        data = api_get(SEARCH_URL, params={
            "q": query, "sort": "updated", "order": "desc", "per_page": 100,
        })
        time.sleep(2.5)  # l'API de recherche est limitée à ~30 req/min
        if data is None:
            continue

        print(f"📊 Résultats trouvés : {data.get('total_count', 0)}")
        for repo in data.get("items", []):
            repo_id = repo.get("id")
            if repo_id is not None and repo_id not in repositories:
                repositories[repo_id] = build_record(repo, query)


def collect_by_location(locations, repositories):
    """Cherche les devs par lieu (search/users), puis liste leurs dépôts récents."""
    for place in locations:
        print(f"\n📍 Devs localisés : {place}")
        data = api_get(USERS_SEARCH_URL, params={
            "q": f'location:"{place}" repos:>0',
            "sort": "followers", "order": "desc", "per_page": USERS_PER_LOCATION,
        })
        time.sleep(2.5)
        if data is None:
            continue

        for user in data.get("items", []):
            repos = api_get(USER_REPOS_URL.format(login=user["login"]), params={
                "type": "owner", "sort": "pushed", "direction": "desc",
                "per_page": REPOS_PER_USER,
            })
            time.sleep(0.15)

            for repo in repos or []:
                if repo.get("fork") or (repo.get("pushed_at") or "") < LOCATION_SINCE:
                    continue
                repo_id = repo.get("id")
                if repo_id is not None and repo_id not in repositories:
                    repositories[repo_id] = build_record(repo, f"location:{place} (users)")


def run_collector():
    print("🤖 Le robot GitHub démarre...")

    queries = [
        "africa package pushed:>=2024-01-01",
        "africa african open-source-africa in:readme package pushed:>=2025-01-01",
        "africa in:readme package pushed:>=2025-01-01",
    ]

    # Dictionnaire pour éliminer les doublons
    repositories = {}

    collect_by_keywords(queries, repositories)
    collect_by_location(LOCATIONS, repositories)

    # Trier les résultats par date de push
    items = sorted(
        repositories.values(),
        key=lambda x: x.get("pushed_at") or "",
        reverse=True,
    )

    data = {
        "source": "github-actions-bot",
        "last_update": datetime.now(timezone.utc).isoformat(),
        "queries_used": queries + [f"location:{p} (users)" for p in LOCATIONS],
        "total_found": len(items),
        "items": items,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 40)
    print(f"✅ Collecte terminée : {len(items)} dépôts uniques")
    print("=" * 40)


if __name__ == "__main__":
    run_collector()
