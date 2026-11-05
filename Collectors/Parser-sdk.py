import base64
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from Path import pathlib

import requests

API_URL = "https://api.github.com/search/repositories"
GITHUB_API = "https://api.github.com"

# Facultatif : un token augmente les limites de l'API GitHub.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Limite les appels complémentaires pour éviter de dépasser trop vite
# les quotas de l'API. Mets None pour enrichir tous les dépôts trouvés.
MAX_ENRICHED_REPOS = 100

SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
})
if GITHUB_TOKEN:
    SESSION.headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"


def api_get(url, params=None):
    """Effectue une requête GitHub et renvoie le JSON, ou None en cas d'échec."""
    try:
        response = SESSION.get(url, params=params, timeout=30)

        if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
            reset_at = response.headers.get("X-RateLimit-Reset")
            print(f"⚠️ Quota GitHub atteint. Réinitialisation Unix : {reset_at}")
            return None

        response.raise_for_status()
        return response.json()

    except requests.exceptions.HTTPError as exc:
        print(f"⚠️ Erreur HTTP sur {url}: {exc}")
    except requests.exceptions.RequestException as exc:
        print(f"⚠️ Erreur réseau sur {url}: {exc}")
    except ValueError:
        print(f"⚠️ Réponse JSON invalide : {url}")

    return None


def extract_documentation_url(readme_text, homepage):
    """Repère un lien de documentation dans le README ; utilise homepage en repli."""
    patterns = [
        r'\[[^\]]*(?:documentation|docs|doc)[^\]]*\]\((https?://[^)\s]+)\)',
        r'https?://[^\s<>)"]*(?:docs|documentation)[^\s<>)"]*',
    ]

    for pattern in patterns:
        match = re.search(pattern, readme_text or "", flags=re.IGNORECASE)
        if match:
            return match.group(1) if match.lastindex else match.group(0)

    return homepage or None


def decode_readme(readme_data):
    """Décode le contenu Base64 renvoyé par l'API GitHub."""
    if not readme_data or not readme_data.get("content"):
        return None

    try:
        raw = base64.b64decode(readme_data["content"])
        return raw.decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return None


def enrich_repository(repo, owner_cache):
    """Complète les champs qui nécessitent des endpoints GitHub supplémentaires."""
    owner = repo.get("owner") or {}
    owner_login = owner.get("login")
    full_name = repo.get("full_name")

    # Les informations publiques du propriétaire sont mises en cache.
    owner_data = None
    if owner_login:
        if owner_login not in owner_cache:
            owner_cache[owner_login] = api_get(
                f"{GITHUB_API}/users/{owner_login}"
            )
        owner_data = owner_cache[owner_login] or {}

    readme_data = api_get(f"{GITHUB_API}/repos/{full_name}/readme") if full_name else None
    readme_text = decode_readme(readme_data)

    releases = api_get(
        f"{GITHUB_API}/repos/{full_name}/releases/latest"
    ) if full_name else None

    license_data = repo.get("license") or {}
    license_name = license_data.get("spdx_id") or license_data.get("name")

    # Une absence de release ne signifie pas que le projet n'a pas de version :
    # on laisse donc le champ vide plutôt que d'inventer une valeur.
    version = (releases or {}).get("tag_name")

    return {
        "primary_language": repo.get("language"),
        "documentation_url": extract_documentation_url(
            readme_text, repo.get("homepage")
        ),
        "version": version,
        "license": license_name,
        "stars": repo.get("stargazers_count", 0),
        # GitHub ne fournit pas de notes moyennes ni de nombre d'avis
        # comparables à ceux d'une plateforme d'évaluation.
        "reviews_count": None,
        "average_rating": None,
        "_location_": owner_data.get("location"),
        "_forks_": repo.get("forks_count", 0),
        "_readme_length_": len(readme_text) if readme_text is not None else None,
        "_website_": repo.get("homepage") or None,
        "_licence_": license_name,
        "_depreciated_": bool(repo.get("archived", False)),
        "archived": bool(repo.get("archived", False)),
        "created_at": repo.get("created_at"),
        "pushed_at": repo.get("pushed_at"),
        "topics": repo.get("topics", []),
        "full_name": full_name,
        "default_branch": repo.get("default_branch"),
        "open_issues_count": repo.get("open_issues_count", 0),
        "owner_type": owner.get("type"),
        "author_email": owner_data.get("email"),
        "author_profile_url": owner_data.get("html_url"),
        "author_avatar_url": owner_data.get("avatar_url"),
        "readme_url": (readme_data or {}).get("html_url"),
        "release_url": (releases or {}).get("html_url"),
    }


def run_collector():
    print("🤖 Le robot GitHub démarre...")

    one_year_ago = (
        datetime.now(timezone.utc) - timedelta(days=365)
    ).strftime("%Y-%m-%d")

    # Plusieurs recherches indépendantes
    queries = [
        f"fintech-africa pushed:>={one_year_ago}",
        f"africa-api african-api open-source-africa pushed:>={one_year_ago}",
        f"africa-tech african-tech africa-sdk pushed:>={one_year_ago}",
        f"africa package pushed:>={one_year_ago}",
        f"africa in:readme sdk pushed:>={one_year_ago}",
        f"open-source-africa in:readme package pushed:>=2024-01-01",
        f"africa-tech african-tech africa-sdk in:readme package pushed:>=2024-01-01",
        "location:africa",
        "location:ghana",
        "location:kenya",
        "location:nigeria",
        "location:senegal",
        "location:ivory-coast",
        "location:cameroon",
        "location:morocco",
        "location:egypt",
        "location:rwanda",
        "mozambique in:location",
    ]

    repositories = {}
    owner_cache = {}
    crawled_at = datetime.now(timezone.utc).isoformat()

    for query in queries:
        print(f"\n🔍 Recherche : {query}")

        params = {
            "q": query,
            "sort": "updated",
            "order": "desc",
            "per_page": 100,
        }

        search_data = api_get(API_URL, params=params)
        if search_data is None:
            continue

        total = search_data.get("total_count", 0)
        print(f"📊 Résultats annoncés : {total}")

        # Conservation volontaire de la clé "libraries" utilisée par ce script.
        for repo in search_data.get("libraries", []):
            repo_id = repo.get("id")
            if repo_id is None or repo_id in repositories:
                continue

            license_data = repo.get("license") or {}
            repositories[repo_id] = {
                "id": repo_id,
                "title": repo.get("name"),
                "description": repo.get("description"),
                "author_name": (repo.get("owner") or {}).get("login"),
                "_source_url_": repo.get("html_url"),
                "stars": repo.get("stargazers_count", 0),
                "primary_language": repo.get("language"),
                "documentation_url": repo.get("homepage") or None,
                "version": None,
                "license": license_data.get("spdx_id") or license_data.get("name"),
                "reviews_count": None,
                "average_rating": None,
                "_location_": None,
                "_forks_": repo.get("forks_count", 0),
                "_readme_length_": None,
                "_website_": repo.get("homepage") or None,
                "_licence_": license_data.get("spdx_id") or license_data.get("name"),
                "_depreciated_": bool(repo.get("archived", False)),
                "archived": bool(repo.get("archived", False)),
                "created_at": repo.get("created_at"),
                "updated_at": repo.get("updated_at"),
                "pushed_at": repo.get("pushed_at"),
                "_last_activity_": repo.get("pushed_at"),
                "topics": repo.get("topics", []),
                "full_name": repo.get("full_name"),
                "default_branch": repo.get("default_branch"),
                "open_issues_count": repo.get("open_issues_count", 0),
                "owner_type": (repo.get("owner") or {}).get("type"),
                "author_email": None,
                "author_profile_url": (repo.get("owner") or {}).get("html_url"),
                "author_avatar_url": (repo.get("owner") or {}).get("avatar_url"),
                "readme_url": None,
                "release_url": None,
                "_crawled_at_": crawled_at,
                "q?": query,
            }

    # Enrichissement des dépôts uniques avec README, release et profil propriétaire.
    items = sorted(
        repositories.values(),
        key=lambda item: item.get("pushed_at") or "",
        reverse=True,
    )

    enrich_limit = (
        len(items) if MAX_ENRICHED_REPOS is None
        else min(len(items), MAX_ENRICHED_REPOS)
    )

    print(f"\n🔧 Enrichissement de {enrich_limit} dépôts sur {len(items)}...")
    for index, item in enumerate(items[:enrich_limit], start=1):
        print(f"   [{index}/{enrich_limit}] {item.get('full_name')}")
        repo_data = {
            "id": item["id"],
            "name": item["title"],
            "full_name": item["full_name"],
            "owner": {
                "login": item["author_name"],
                "type": item["owner_type"],
            },
            "language": item["primary_language"],
            "homepage": item["_website_"],
            "license": {
                "spdx_id": item["_licence_"],
                "name": item["_licence_"],
            } if item["_licence_"] else None,
            "stargazers_count": item["stars"],
            "forks_count": item["_forks_"],
            "archived": item["archived"],
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
            "pushed_at": item["pushed_at"],
            "topics": item["topics"],
            "default_branch": item["default_branch"],
            "open_issues_count": item["open_issues_count"],
            "html_url": item["_source_url_"],
        }

        enriched = enrich_repository(repo_data, owner_cache)
        item.update(enriched)

        # Les champs de date et d'activité proviennent du dépôt GitHub.
        item["updated_at"] = repo_data.get("updated_at")
        item["_last_activity_"] = repo_data.get("pushed_at")

        # Petite pause pour éviter d'enchaîner trop brutalement les requêtes.
        time.sleep(0.15)

    # Les dépôts au-delà de la limite gardent les données disponibles
    # dans la réponse de recherche ; les champs complémentaires restent None.
    data = {
        "source": "github-actions-bot",
        "last_update": datetime.now(timezone.utc).isoformat(),
        "queries_used": queries,
        "total_found": len(items),
        "libraries": items,
    }
    Racine du repo = dossier parent de Collectors/
    BASE_DIR = Path(__file__).resolve().parent.parent
    SDKS_PATH = BASE_DIR / "data" / "SDKs.json"
    
    with open(SDKS_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    print("\n" + "=" * 40)
    print(f"✅ Collecte terminée : {len(items)} dépôts uniques")
    print(f"🔧 Dépôts enrichis : {enrich_limit}")
    print("=" * 40)


if __name__ == "__main__":
    run_collector()
