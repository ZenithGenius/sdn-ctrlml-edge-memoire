"""Couche Analyse #1 — lissage EMA (CCDS `features.py`).

L'EMA élimine le bruit des mesures brutes avant classification. Réutilise
`pandas.DataFrame.ewm` : aucune formule codée à la main (cf. plan).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


def ema(frame: pd.DataFrame | pd.Series, alpha: float = config.EMA_ALPHA):
    """Moyenne mobile exponentielle. `EMAt = α·Xt + (1−α)·EMA(t−1)` via pandas.

    `adjust=False` est indispensable : c'est la forme récursive exacte du mémoire ;
    le défaut pandas (`adjust=True`) calcule une moyenne pondérée normalisée différente.
    """
    return frame.ewm(alpha=alpha, adjust=False).mean()


def smoothed_vectors(
    history: list[dict[int, dict[str, float]]],
    controllers: list[int],
    alpha: float = config.EMA_ALPHA,
    metrics: list[str] = config.METRICS,
) -> np.ndarray:
    """Construit la matrice [n_contrôleurs × n_métriques] des valeurs EMA courantes.

    `history` : liste de snapshots (une par époque de ré-clustering) ;
    chaque snapshot = {contrôleur: {métrique: valeur}}. Pour chaque métrique on
    lisse la série temporelle par contrôleur et on prend la dernière valeur lissée.
    """
    vectors = np.zeros((len(controllers), len(metrics)))
    for m_idx, metric in enumerate(metrics):
        # DataFrame [époque × contrôleur] pour cette métrique
        series = pd.DataFrame(
            [[snap[c][metric] for c in controllers] for snap in history],
            columns=controllers,
        )
        last_ema = ema(series, alpha).iloc[-1]
        vectors[:, m_idx] = last_ema.to_numpy()
    return vectors
