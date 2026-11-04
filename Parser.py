import os, datetime
import requests

def run_collector():
    print("🤖 Le robot de collecte démarre...")
    
    # Données simulées (qu'on enverra bientôt à FastAPI)
    payload_data = {
        "source": "github-actions-bot",
        "items": [
            {
                "title": "Exemple de projet Tech Africain",
                "type": "sdk",
                "date": datetime.datetime.now() 
                "github_url": "https://github.com/exemple/projet"
            }
        ]
    }
    
    print("Données prêtes à être envoyées :", payload_data)
    print("✅ Collecte terminée avec succès !")

if __name__ == "__main__":
    run_collector()
