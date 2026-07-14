# SDN_CTRLML-Edge

Simulateur d'**équilibrage de charge SDN décentralisé pour infrastructure Cloud-Edge**.
Réalise et valide la contribution du mémoire (`docs/`) : extension de SDN_CTRLML
(Aly et al., 2025) combinant **EMA** (lissage multi-métriques) + **K-means++**
(classification des contrôleurs par profil de charge) + une **couche décisionnelle**
consciente de la latence.

## Principe

`Métriques → EMA (lissage) → K-means++ (sous-utilisé/normal/surchargé) → décision → rééquilibrage`

Là où SDN_CTRLML n'équilibre que sur le **CPU** (contrôleurs supposés homogènes),
SDN_CTRLML-Edge utilise **6 métriques** (CPU, mémoire, bande passante, latence, nombre
de flux, intensité Packet-In normalisée par la capacité λ/µ), les lisse par **EMA
récursive** (`adjust=False`, la formule exacte du mémoire) et décide en **deux régimes**
(objectif 6 du mémoire, « le plus proche ET le moins chargé ») :

- **régime normal** : contrôleur le plus proche en latence parmi les contrôleurs sains
  (hors cluster surchargé K-means++ **et** sous le seuil individuel `OVERLOAD_CPU`) ;
- **régime saturé** (aucun contrôleur sain) : délestage pur vers le moins chargé —
  quand tout est chaud, l'attente domine la latence réseau.

L'infrastructure est réellement hétérogène : contrôleurs stratifiés **cloud / fog /
edge** (µ = 6000 / 3500 / 1800 req/s), latences issues des positions géographiques.

## Structure (Cookiecutter Data Science)

```
data/{raw,interim,processed,external}   # datasets (réels si déposés, sinon repli synthétique)
docs/                                   # mémoire + article de référence
reports/figures/                        # figures générées
sdn_ctrl_ml_edge/
├── config.py        # chemins + hyperparamètres
├── dataset.py       # infrastructure (topologie networkx) + ingestion (Alibaba/InSDN/Topology Zoo)
├── features.py      # EMA (pandas.ewm)
├── modeling/
│   ├── clustering.py  # K-means++ (sklearn) + silhouette
│   ├── decision.py    # stratégie proposée (SDN_CTRLML-Edge)
│   └── baselines.py   # onos / sdnctrlml / rr / random
├── simulate.py      # moteur de simulation (file M/M/1, charge mise à jour par flux)
├── plots.py         # figures
├── evaluate.py      # balayage déterministe rapide (smoke) → results.csv + figures
├── experiment.py    # plan d'expériences ~1000 runs multi-seeds (joblib) + MLflow
└── analysis.py      # IC95 (Student) + Wilcoxon appariés + figures + rapport Markdown
tests/               # tests de vérification bout-en-bout
```

> Les figures destinées au mémoire sont celles de `analysis` (multi-seeds, barres
> d'erreur) dans `reports/figures/analysis/` — pas celles du balayage mono-seed
> `evaluate` (simple contrôle rapide).

## Datasets réutilisés

Le pipeline tourne hors-ligne (repli synthétique calibré). Pour utiliser les données
réelles, déposer :

| Fichier | Source |
|---|---|
| `data/external/geant.graphml` | [Internet Topology Zoo](https://topology-zoo.org/dataset.html) |
| `data/raw/alibaba_server_usage.csv` | [Alibaba cluster trace](https://github.com/alibaba/clusterdata) |
| `data/raw/insdn_flows.csv` | InSDN (dataset SDN, Kaggle) |

## Utilisation

```bash
make venv requirements        # environnement (.venv)
make test                     # vérification
make simulate STRATEGY=edge RATE=16000
make evaluate                 # balayage rapide mono-seed (smoke) + figures
make experiment SEEDS=25      # 1000 runs multi-seeds + tracking MLflow (sqlite)
make analyze                  # IC95 + tests de Wilcoxon + rapport + figures
make mlflow-ui                # UI MLflow → http://127.0.0.1:5000
```

Ou directement :

```bash
.venv/bin/python -m sdn_ctrl_ml_edge.simulate --strategy edge --arrival-rate 16000
.venv/bin/python -m sdn_ctrl_ml_edge.evaluate
```

## Périmètre

Cœur : équilibrage EMA + K-means++ multi-métriques. Hors périmètre (→ Perspectives) :
placement RL des contrôleurs, prédiction LSTM du trafic, détection DDoS.
