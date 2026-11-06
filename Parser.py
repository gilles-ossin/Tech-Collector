
import json
from datetime import datetime, timedelta, timezone
import requests

API_URL = "https://api.github.com/search/repositories"

def run_collector():
    print("🤖 Le robot GitHub démarre...")

    one_year_ago = (
        datetime.now(timezone.utc) - timedelta(days=365)
    ).strftime("%Y-%m-%d")

    # Plusieurs recherches indépendantes
    queries = [
       "location:ivory-coast",
       "location:rwanda",
       "location:morocco",
       "africa package pushed:>=2024-01-01",
       "africa african open-source-africa in:readme package pushed:>=2025-01-01",
       "africa in:readme package pushed:>=2025-01-01"
  ],
    headers = {
        "Accept": "application/vnd.github+json"
    }

    # Dictionnaire pour éliminer les doublons
    repositories = {}

    for query in queries:
        print(f"\n🔍 Recherche : {query}")

        params = {
            "q": query,
            "sort": "updated",
            "order": "desc",
            "per_page": 100
        }

        try:
            response = requests.get(
                API_URL,
                params=params,
                headers=headers,
                timeout=30
            )

            response.raise_for_status()
            search_data = response.json()

            total = search_data.get("total_count", 0)

            print(f"📊 Résultats trouvés : {total}")

            for repo in search_data.get("items", []):

                repo_id = repo.get("id")

                if repo_id in repositories:
                    continue

                repositories[repo_id] = {
                    "title": repo.get("name"),
                    "description": repo.get("description"),
                    "type": "SDK / Package",
                    "github_url": repo.get("html_url"),
                    "updated_at": repo.get("updated_at"),
                    "pushed_at": repo.get("pushed_at"),
                    "stars": repo.get("stargazers_count"),
                    "author": repo.get("owner", {}).get("login"),
                    "q?": query
                }

        except requests.exceptions.HTTPError as e:
            print(f"❌ Erreur HTTP : {e}")
            print(response.text[:500])

        except requests.exceptions.RequestException as e:
            print(f"❌ Erreur réseau : {e}")

        except ValueError as e:
            print(f"❌ Erreur JSON : {e}")

    # Trier les résultats par date de push
    items = sorted(
        repositories.values(),
        key=lambda x: x.get("pushed_at") or "",
        reverse=True
    )

    # Structure finale
    data = {
        "source": "github-actions-bot",
        "last_update": datetime.now(timezone.utc).isoformat(),
        "queries_used": queries,
        "total_found": len(items),
        "items": items
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 40)
    print(f"✅ Collecte terminée : {len(items)} dépôts uniques")
    print("=" * 40)


if __name__ == "__main__":
    run_collector()
