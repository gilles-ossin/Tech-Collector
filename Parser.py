import json
import os
from datetime import datetime, timedelta
import requests

def run_collector():
    print("🤖 Le robot de collecte démarre...")
    
    # 1. Calculer la date d'il y a 1 an (pour respecter le filtre "récent d'au plus 1 an")
    one_year_ago = (datetime.utcnow() - timedelta(days=365)).strftime('%Y-%m-%d')
    
    # 2. Construire la requête pour l'API GitHub
    # On cherche les mots-clés "africa", "sdk" ou "package" mis à jour après la date calculée
    query = f"africa sdk package librair library pushed:>{one_year_ago}"
    url = f"https://api.github.com/search/repositories?q={query}&sort=updated&order=desc"
    
    print(f"🔍 Interrogation de l'API GitHub : {query}")
    
    try:
        # Appel à l'API GitHub (sans authentification, limité à 10 requêtes/minute, largement suffisant pour un cron toutes les 6h)
        headers = {"Accept": "application/vnd.github.v3+json"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        search_data = response.json()
        
        # 3. Extraire les informations utiles des dépôts trouvés
        items = []
        for repo in search_data.get("items", [])[:15]: # On garde les 15 plus pertinents/récents
            items.append({
                "title": repo.get("name"),
                "description": repo.get("description"),
                "type": "SDK / Package",
                "github_url": repo.get("html_url"),
                "updated_at": repo.get("updated_at"),
                "stars": repo.get("stargazers_count"),
                "author": repo.get("owner", {}).get("login")
            })
            
        # Structure finale du fichier data.json
        data = {
            "source": "github-actions-bot",
            "last_update": datetime.utcnow().isoformat(),
            "query_used": query,
            "total_found": search_data.get("total_count", 0),
            "items": items
        }
        
        # 4. Écriture dans le fichier data.json à la racine
        with open("data.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        print(f"✅ Succès ! {len(items)} projets récents trouvés et enregistrés dans data.json.")
        
    except Exception as e:
        print(f"❌ Erreur lors de la collecte GitHub : {e}")

if __name__ == "__main__":
    run_collector()
