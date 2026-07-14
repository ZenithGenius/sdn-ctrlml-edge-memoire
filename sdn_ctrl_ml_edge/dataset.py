"""Couche Infrastructure + ingestion des données (CCDS `dataset.py`).

Réutilise des jeux de données réels s'ils sont présents dans `data/`, sinon
retombe sur un générateur synthétique calibré — le pipeline reste exécutable
hors-ligne (cf. plan, §« Robustesse offline »).

Sources réelles attendues (déposer les fichiers pour les activer) :
- Topology Zoo   : data/external/<nom>.graphml  (https://topology-zoo.org)
- Alibaba trace  : data/raw/alibaba_server_usage.csv  (github.com/alibaba/clusterdata)
- InSDN          : data/raw/insdn_flows.csv  (dataset SDN, Kaggle)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandas as pd

from . import config

# Latence : conversion distance géographique normalisée -> millisecondes.
_LATENCY_SCALE_MS = 40.0
_LATENCY_BASE_MS = 0.5


# --------------------------------------------------------------------------- #
# Topologie réseau
# --------------------------------------------------------------------------- #
def _edge_latency(g: nx.Graph) -> None:
    """Annote chaque arête d'une latence (ms) à partir des positions des nœuds."""
    for u, v in g.edges():
        pu, pv = g.nodes[u].get("pos"), g.nodes[v].get("pos")
        if pu is not None and pv is not None:
            d = math.dist(pu, pv)
            g.edges[u, v]["latency"] = _LATENCY_BASE_MS + d * _LATENCY_SCALE_MS
        else:
            g.edges[u, v]["latency"] = 1.0


def _synthetic_topology(n: int = 40, seed: int = config.RANDOM_SEED) -> nx.Graph:
    """Backbone géographique ~GÉANT : positions aléatoires + MST (connexité) + voisinage."""
    rng = np.random.default_rng(seed)
    pos = {i: (float(rng.random()), float(rng.random())) for i in range(n)}

    # Graphe complet pondéré par la distance -> arbre couvrant minimal (connexité garantie)
    full = nx.Graph()
    for i in range(n):
        for j in range(i + 1, n):
            full.add_edge(i, j, weight=math.dist(pos[i], pos[j]))
    g = nx.minimum_spanning_tree(full)

    # Densifier : relier chaque nœud à ses 2 plus proches voisins (mailles de backbone)
    for i in range(n):
        dists = sorted(((math.dist(pos[i], pos[j]), j) for j in range(n) if j != i))
        for _, j in dists[:2]:
            g.add_edge(i, j)

    for i in range(n):
        g.nodes[i]["pos"] = pos[i]
    _edge_latency(g)
    return g


def load_topology(name: str = config.DEFAULT_TOPOLOGY) -> nx.Graph:
    """Charge un GraphML réel (data/external/<name>.graphml) sinon topologie synthétique."""
    path = config.EXTERNAL_DIR / f"{name}.graphml"
    if path.exists():
        g = nx.read_graphml(path)
        g = nx.convert_node_labels_to_integers(g, label_attribute="name")
        for i in g.nodes():
            lat, lon = g.nodes[i].get("Latitude"), g.nodes[i].get("Longitude")
            if lat is not None and lon is not None:
                g.nodes[i]["pos"] = (float(lon), float(lat))
        # normaliser les positions dans [0,1]^2 pour une échelle de latence cohérente
        _rescale_positions(g)
        _edge_latency(g)
        if not nx.is_connected(g):
            g = g.subgraph(max(nx.connected_components(g), key=len)).copy()
        return nx.convert_node_labels_to_integers(g)
    return _synthetic_topology()


def _rescale_positions(g: nx.Graph) -> None:
    pts = [g.nodes[i].get("pos") for i in g.nodes() if g.nodes[i].get("pos")]
    if not pts:
        return
    xs, ys = zip(*pts)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    sx, sy = (x1 - x0) or 1.0, (y1 - y0) or 1.0
    for i in g.nodes():
        p = g.nodes[i].get("pos")
        if p:
            g.nodes[i]["pos"] = ((p[0] - x0) / sx, (p[1] - y0) / sy)


# --------------------------------------------------------------------------- #
# Infrastructure Cloud-Edge (nœuds, tiers, contrôleurs, latences)
# --------------------------------------------------------------------------- #
@dataclass
class Infrastructure:
    graph: nx.Graph
    controllers: list[int]
    capacity: dict[int, float]                 # débit de service µ (req/s) par contrôleur
    tier: dict[int, str]                       # 'cloud' / 'fog' / 'edge'
    latency: dict[int, dict[int, float]]       # latency[node][controller] (ms)
    nodes: list[int] = field(default_factory=list)

    def nearest_controller(self, node: int) -> int:
        lat = self.latency[node]
        return min(self.controllers, key=lat.get)


def build_infrastructure(
    name: str = config.DEFAULT_TOPOLOGY,
    n_controllers: int = config.N_CONTROLLERS,
    seed: int = config.RANDOM_SEED,
) -> Infrastructure:
    """Construit l'infrastructure : place les contrôleurs sur les nœuds les plus centraux
    et hétérogènes (Cloud/Fog puissants), calcule les latences plus-court-chemin."""
    g = load_topology(name)
    nodes = list(g.nodes())
    n_controllers = min(n_controllers, len(nodes))

    # Tier par centralité de degré : les plus connectés = Cloud, puis Fog, puis Edge.
    deg = dict(g.degree())
    ranked = sorted(nodes, key=lambda i: deg[i], reverse=True)
    n_cloud = max(1, len(nodes) // 6)
    n_fog = max(1, len(nodes) // 3)
    tier, capacity = {}, {}
    for r, i in enumerate(ranked):
        if r < n_cloud:
            tier[i], capacity[i] = "cloud", 6000.0
        elif r < n_cloud + n_fog:
            tier[i], capacity[i] = "fog", 3500.0
        else:
            tier[i], capacity[i] = "edge", 1800.0

    # Contrôleurs stratifiés sur les trois tiers — le cœur du récit Cloud-Edge :
    # des contrôleurs Cloud puissants ET des contrôleurs Edge contraints (µ hétérogènes).
    # ~1/3 cloud, ~1/3 fog, le reste edge ; dans chaque tier, les plus centraux d'abord.
    by_tier = {t: [i for i in ranked if tier[i] == t] for t in ("cloud", "fog", "edge")}
    quota = {
        "cloud": max(1, n_controllers // 3),
        "fog": max(1, n_controllers // 3),
    }
    quota["edge"] = max(1, n_controllers - quota["cloud"] - quota["fog"])
    controllers = []
    for t in ("cloud", "fog", "edge"):
        controllers.extend(by_tier[t][: quota[t]])
    # Compléter si un tier n'avait pas assez de nœuds.
    for i in ranked:
        if len(controllers) >= n_controllers:
            break
        if i not in controllers:
            controllers.append(i)
    controllers = controllers[:n_controllers]

    # Latences plus-court-chemin (pondérées) de chaque nœud vers chaque contrôleur.
    latency: dict[int, dict[int, float]] = {}
    for c in controllers:
        dist = nx.single_source_dijkstra_path_length(g, c, weight="latency")
        for node in nodes:
            latency.setdefault(node, {})[c] = dist.get(node, 1e6)

    return Infrastructure(
        graph=g, controllers=controllers, capacity={c: capacity[c] for c in controllers},
        tier=tier, latency=latency, nodes=nodes,
    )


# --------------------------------------------------------------------------- #
# Charge de fond hétérogène (Alibaba cluster trace, sinon synthétique calibré)
# --------------------------------------------------------------------------- #
def load_baseline_load(
    controllers: list[int], n_steps: int, seed: int = config.RANDOM_SEED
) -> pd.DataFrame:
    """Séries temporelles d'utilisation de fond [0,1] par contrôleur (hétérogénéité réelle).

    Réutilise Alibaba `server_usage` si présent (colonnes cpu_util en %), sinon génère
    des profils hétérogènes (contrôleurs Cloud peu chargés, Edge davantage) avec du bruit.
    """
    path = config.RAW_DIR / "alibaba_server_usage.csv"
    if path.exists():
        df = pd.read_csv(path)
        col = next((c for c in df.columns if "cpu" in c.lower()), df.columns[-1])
        series = df[col].to_numpy(dtype=float)
        series = series / (100.0 if series.max() > 1.5 else 1.0)  # % -> [0,1]
        out = {}
        for k, c in enumerate(controllers):
            start = (k * 997) % max(1, len(series) - n_steps)
            seg = series[start:start + n_steps]
            if len(seg) < n_steps:  # pad par répétition
                seg = np.resize(seg, n_steps)
            out[c] = seg
        return pd.DataFrame(out)

    rng = np.random.default_rng(seed)
    out = {}
    for c in controllers:
        base = rng.uniform(0.1, 0.5)                       # niveau de fond hétérogène
        trend = np.cumsum(rng.normal(0, 0.01, n_steps))    # marche lente
        noise = rng.normal(0, 0.05, n_steps)               # pics transitoires (bruit à lisser par EMA)
        out[c] = np.clip(base + trend + noise, 0.0, 0.95)
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- #
# Flux réseau (InSDN si présent, sinon caractéristiques synthétiques)
# --------------------------------------------------------------------------- #
def load_flow_stream(
    n_flows: int, nodes: list[int], seed: int = config.RANDOM_SEED
) -> pd.DataFrame:
    """Flux à ordonnancer : source (nœud), coût de traitement. Réutilise InSDN si présent."""
    rng = np.random.default_rng(seed)
    src = rng.choice(nodes, size=n_flows)

    path = config.RAW_DIR / "insdn_flows.csv"
    if path.exists():
        df = pd.read_csv(path)
        pkt_col = next((c for c in df.columns
                        if "pkt" in c.lower() or "packet" in c.lower()), None)
        if pkt_col is not None:
            w = df[pkt_col].to_numpy(dtype=float)
            w = np.resize(w[np.isfinite(w) & (w > 0)], n_flows)
            cost = 0.5 + (w / np.median(w))            # coût relatif au flux médian
            return pd.DataFrame({"src": src, "cost": np.clip(cost, 0.2, 5.0)})

    # Coûts synthétiques : distribution longue-traîne (quelques flux lourds).
    cost = np.clip(rng.lognormal(mean=0.0, sigma=0.6, size=n_flows), 0.2, 5.0)
    return pd.DataFrame({"src": src, "cost": cost})
