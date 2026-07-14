"""Couche Analyse #2 — classification K-means++ des contrôleurs par profil de charge.

Réutilise `sklearn.cluster.KMeans` (init `k-means++` par défaut) et `silhouette_score`.
Étiquette les clusters en sous-utilisé / normal / surchargé selon le niveau de charge
du centroïde — c'est ce que la couche décisionnelle exploite pour rééquilibrer.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from .. import config

# Métriques indiquant une charge (croissantes) ; la latence n'en fait pas partie.
_LOAD_METRICS = ["cpu", "mem", "bw", "nflows", "packetin"]


@dataclass
class ClusterResult:
    labels: np.ndarray                 # cluster de chaque contrôleur
    load_score: dict[int, float]       # score de charge par cluster (standardisé)
    overloaded: set[int]               # cluster le plus chargé
    underutilized: set[int]            # cluster le moins chargé
    silhouette: float                  # qualité du partitionnement (NaN si non calculable)
    k: int


def classify(
    vectors: np.ndarray,
    k: int = config.N_CLUSTERS,
    metrics: list[str] = config.METRICS,
    seed: int = config.RANDOM_SEED,
) -> ClusterResult:
    """Classe les vecteurs de charge (EMA) en k profils via K-means++."""
    n = len(vectors)
    k = max(1, min(k, n))

    scaler = StandardScaler()
    x = scaler.fit_transform(vectors)

    if k == 1:
        return ClusterResult(np.zeros(n, dtype=int), {0: 0.0}, set(), {0}, float("nan"), 1)

    km = KMeans(n_clusters=k, init="k-means++", n_init=10, random_state=seed)
    labels = km.fit_predict(x)

    # Score de charge d'un cluster = moyenne du centroïde (standardisé) sur les métriques de charge.
    load_idx = [metrics.index(m) for m in _LOAD_METRICS if m in metrics]
    load_score = {c: float(km.cluster_centers_[c, load_idx].mean()) for c in range(k)}

    hi = max(load_score, key=load_score.get)
    lo = min(load_score, key=load_score.get)

    # Seuil absolu : « surchargé » = cluster le plus chargé ET CPU moyen (brut, EMA)
    # au-dessus de OVERLOAD_CPU. Sans lui, on exclurait des contrôleurs sains à faible
    # charge (le max relatif existe toujours).
    cpu_idx = metrics.index("cpu")
    hi_cpu = float(vectors[labels == hi, cpu_idx].mean())
    overloaded = {hi} if hi_cpu >= config.OVERLOAD_CPU else set()

    sil = float("nan")
    if 1 < k < n and len(set(labels)) > 1:
        sil = float(silhouette_score(x, labels))

    return ClusterResult(labels, load_score, overloaded, {lo}, sil, k)
