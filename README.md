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
├── analysis.py      # IC95 (Student) + Wilcoxon appariés + figures + rapport Markdown
├── flowviz.py       # visualisation des stratégies (simulation)
├── topoviz.py       # topologie mesurée : networkx + pyvis (interactif) / matplotlib
└── emulation/       # banc d'essai OpenFlow : contrôleurs os-ken + rééquilibreur
docker/              # Dockerfile et docker compose (Linux, macOS, Windows)
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
```

Sous **Windows**, `make` n'existe pas : appelez directement les modules, en
remplaçant `.venv/bin/python` par `.venv\Scripts\python`.

```bash
.venv/bin/python -m sdn_ctrl_ml_edge.simulate --strategy edge --arrival-rate 16000
```

### Consulter les résultats

Aucun fichier de sortie n'est versionné : tout se régénère. Les résultats
consolidés se lisent dans `reports/` (`aggregated.csv`, `significance.csv`,
`analysis_report.md`).

```bash
make mlflow-ui                # UI MLflow -> http://127.0.0.1:5000
```

MLflow contient les **1 000 exécutions** du plan d'expériences, avec sept
métriques de sortie (temps de réponse, débit, CPU, équité de Jain, migrations,
taux de surcharge, silhouette). Il faut avoir lancé `make experiment` au
préalable, sinon la base est vide.

### Collecter les six métriques d'entrée

Les métriques agrégées ne montrent pas ce que consomme réellement la couche
d'analyse. Cette commande exporte, par contrôleur et par époque, les six
métriques brutes, leurs valeurs lissées par EMA et le profil attribué :

```bash
.venv/bin/python -m sdn_ctrl_ml_edge.simulate --metrics-out reports/metriques.csv
```

### Visualisations

```bash
make flowviz                  # reports/flux_sdn.html : comparaison des stratégies
.venv/bin/python -m sdn_ctrl_ml_edge.topoviz \
    --from reports/emulation/trace.json \
    --out reports/topologie.html \
    --static reports/figures/memoire/fig_topologie.png
```

`flowviz` compare les stratégies en simulation. `topoviz` représente une
topologie mesurée en émulation réelle : rendu interactif avec **pyvis** (vis.js)
et figure statique avec **networkx + matplotlib**. Les pages produites sont
autonomes, sans ressource externe, donc utilisables hors ligne. Ouvrez le
fichier HTML par un double-clic.

## Banc d'essai OpenFlow (émulation réelle)

Le simulateur fournit les mesures quantitatives ; l'émulation vérifie que
l'algorithme fonctionne sur de vrais commutateurs Open vSwitch pilotés en
OpenFlow 1.3 (Mininet + contrôleurs os-ken), dans Docker.

```bash
docker compose -f docker/docker-compose.yml run --rm emulate     # avec rééquilibrage
docker compose -f docker/docker-compose.yml run --rm baseline    # sans, pour comparer
docker compose -f docker/docker-compose.yml run --rm sim         # les tests
```

Toute option passée après le nom du service est transmise au banc d'essai :

```bash
docker compose -f docker/docker-compose.yml run --rm emulate \
    --controllers 5 --switches 10 --hosts-per-switch 3 --traffic pics --rounds 12
```

| Option | Défaut | Rôle |
|---|---|---|
| `--controllers` | 3 | nombre de contrôleurs |
| `--switches` | 6 | commutateurs OpenFlow |
| `--hosts-per-switch` | 2 | hôtes par commutateur |
| `--rounds` / `--interval` | 12 / 3 s | nombre et durée des cycles |
| `--traffic` | constant | `constant` ou `pics` |
| `--no-rebalance` | — | référence sans rééquilibrage |
| `--trace-out` | — | trace JSON (écrire dans `/sdn/out`) |

Après modification du code, **reconstruisez l'image** : `run --build` ou
`docker compose -f docker/docker-compose.yml build emulate`.

**Prérequis** : le module noyau `openvswitch` doit être chargé sur l'hôte
(`sudo modprobe openvswitch`). Le noyau WSL2 par défaut ne l'inclut pas : sous
Windows, rejouez plutôt une trace existante avec `topoviz`.

À la première exécution, comptez quelques dizaines de secondes de silence avant
le premier cycle, le temps de démarrer les contrôleurs et le trafic. Le message
`Error setting resource limits` émis par Mininet est sans conséquence.

### Lire le tableau de sortie

```
| tour | Jain  | Jain* |    c0     |    c1     | migr. | switches   |
|      | brut  | pond. |   Cloud   |    Fog    |       | déplacés   |
| 0    | 0.200 | 0.200 |  1315.1   |    0.0    | 7     | s1,s2,s3,s |
| 4    | 0.921 | 0.966 |    55.6   |   37.7    | 0     | -          |
```

- **tour** : cycle de décision (un relevé, puis une décision de rééquilibrage).
- **c0, c1, …** : charge du plan de contrôle mesurée sur l'intervalle, en
  Packet-In par seconde. La seconde ligne d'en-tête rappelle le palier du
  contrôleur (Cloud 6000, Fog 3500, Edge 1800 requêtes/s de capacité).
- **Jain brut** : équité classique sur les charges. Trompeur en environnement
  hétérogène, car il vaudrait 1 si tous les contrôleurs portaient la *même*
  charge, ce qui saturerait le petit contrôleur Edge.
- **Jain\*** : équité **pondérée par la capacité**, la mesure de référence ici.
  Elle vaut 1 quand chaque contrôleur porte une charge proportionnelle à sa
  puissance, c'est-à-dire « à chacun selon sa capacité ».
- **migr.** et **switches déplacés** : réaffectations décidées à ce cycle. La
  colonne est tronquée à dix caractères.
- `n/a` en colonne Jain signale une charge totale négligeable : l'équité n'a
  alors pas de sens et afficher 1 serait trompeur.

Lecture attendue : au tour 0 tout est concentré sur `c0` (Jain\* au plus bas,
0,200 pour cinq contrôleurs) ; l'algorithme migre des commutateurs, puis
**Jain\* remonte au-dessus de 0,95 et les migrations retombent à zéro**, signe
que le système a convergé sans osciller. Avec `--no-rebalance`, Jain\* reste
figé à sa valeur initiale.

## Périmètre

Cœur : équilibrage EMA + K-means++ multi-métriques. Hors périmètre (→ Perspectives) :
placement RL des contrôleurs, prédiction LSTM du trafic, détection DDoS.
