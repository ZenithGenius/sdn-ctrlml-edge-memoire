"""Test bout-en-bout minimal (cf. plan, §Vérification).

Vérifie (a) l'EMA réduit le bruit, (b) K-means++ sépare bien 3 profils,
(c) la stratégie proposée améliore l'équilibrage vs le binding statique ONOS.
Exécutable via `pytest` ou `python -m tests.test_pipeline`.
"""
import numpy as np
import pandas as pd

from sdn_ctrl_ml_edge.features import ema
from sdn_ctrl_ml_edge.modeling.clustering import classify
from sdn_ctrl_ml_edge.simulate import run


def test_ema_reduces_noise():
    rng = np.random.default_rng(0)
    signal = pd.Series(np.repeat([0.2, 0.8], 100) + rng.normal(0, 0.1, 200))
    smoothed = ema(signal, alpha=0.2)
    assert smoothed.var() < signal.var()


def test_kmeanspp_separates_three_profiles():
    rng = np.random.default_rng(0)
    blobs = np.vstack([
        rng.normal(loc, 0.02, size=(4, 6)) for loc in (0.15, 0.5, 0.9)
    ])
    result = classify(blobs, k=3)
    assert result.k == 3
    assert len(set(result.labels)) == 3
    assert result.silhouette > 0.5
    assert result.overloaded != result.underutilized


def test_edge_improves_balance_vs_onos():
    common = dict(arrival_rate=20000.0, n_flows=4000)
    edge = run(strategy="edge", **common)
    onos = run(strategy="onos", **common)
    for m in (edge, onos):
        assert np.isfinite(m["response_time_ms"])
        assert 0.0 < m["jain_fairness"] <= 1.0
    # La proposition équilibre mieux la charge que le binding statique.
    assert edge["jain_fairness"] > onos["jain_fairness"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"OK  {name}")
    print("Tous les tests passent.")
