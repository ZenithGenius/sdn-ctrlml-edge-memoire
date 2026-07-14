"""Vérification du plan d'expériences et de l'analyse statistique (petite grille rapide)."""
from sdn_ctrl_ml_edge.analysis import REPORT_METRICS, aggregate, significance
from sdn_ctrl_ml_edge.experiment import run_doe


def _small_doe():
    return run_doe(
        strategies=["edge", "onos"],
        arrival_rates=[8000, 20000],
        seeds=3,
        n_flows=1500,
        n_jobs=1,
    )


def test_doe_shape_and_columns():
    df = _small_doe()
    assert len(df) == 2 * 2 * 3  # stratégies × taux × seeds
    for col in ["strategy", "arrival_rate", "seed", *REPORT_METRICS]:
        assert col in df.columns


def test_aggregate_has_ci():
    df = _small_doe()
    agg = aggregate(df)
    assert "response_time_ms_mean" in agg.columns
    assert "response_time_ms_ci95" in agg.columns
    assert (agg["n"] == 3).all()


def test_significance_runs():
    df = _small_doe()
    sig = significance(df)
    # une ligne par (baseline × métrique) ; ici 1 baseline (onos) × métriques
    assert set(sig["metric"]) == set(REPORT_METRICS)
    assert (sig["win_rate"].between(0.0, 1.0)).all()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"OK  {name}")
    print("Tests DOE/analyse OK.")
