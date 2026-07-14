"""Chemins et paramètres par défaut (convention Cookiecutter Data Science).

Un seul endroit pour les chemins du projet et les hyperparamètres du modèle
SDN_CTRLML-Edge, importé par tous les autres modules.
"""
from __future__ import annotations

from pathlib import Path

# --- Arborescence du projet (CCDS) ---
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
EXTERNAL_DIR = DATA_DIR / "external"
REPORTS_DIR = PROJECT_ROOT / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"
MODELS_DIR = PROJECT_ROOT / "models"

# --- Métriques multi-critères (contribution du mémoire vs CPU seul) ---
# Ordre stable : sert de colonnes des vecteurs de charge par contrôleur.
METRICS = ["cpu", "mem", "bw", "latency", "nflows", "packetin"]

# --- Analyse intelligente ---
EMA_ALPHA = 0.2          # facteur de lissage EMA (mémoire : 0.1–0.3)
N_CLUSTERS = 3           # sous-utilisé / normal / surchargé
OVERLOAD_CPU = 0.70      # seuil absolu : un cluster n'est « surchargé » que si son
                         # CPU moyen (EMA) dépasse ce seuil — évite d'exclure des
                         # contrôleurs sains quand tout le réseau est peu chargé
RANDOM_SEED = 42

# --- Infrastructure / simulation ---
DEFAULT_TOPOLOGY = "geant"
N_CONTROLLERS = 10       # domaines SDN décentralisés
RECLUSTER_EVERY = 200    # ré-clustering tous les N flux (couche Analyse)

# Balayage des taux d'arrivée (flux/s) pour la figure « temps de réponse vs charge »
ARRIVAL_RATES = [1000, 2000, 4000, 8000, 12000, 16000, 20000, 26000, 32000, 40000]

STRATEGIES = ["edge", "sdnctrlml", "onos", "rr", "random"]
