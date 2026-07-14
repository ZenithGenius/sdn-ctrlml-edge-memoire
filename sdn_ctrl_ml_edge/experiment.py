"""Plan d'expériences à grande échelle (Design of Experiments).

Balaye stratégies × taux d'arrivée × seeds → ~1000 runs, parallélisés sur tous les
cœurs avec joblib, et trace chaque run dans MLflow (params + métriques). Le multi-seed
donne la variance nécessaire aux intervalles de confiance et tests de significativité
du module `analysis` — c'est ce qui rend les conclusions défendables.

Pourquoi joblib et pas Airflow/Spark : 1000 simulations Python indépendantes sur une
machine se parallélisent trivialement sur les cœurs, sans cluster ni ordonnanceur à
maintenir. La rigueur vient du multi-seed + MLflow, pas d'une infra lourde.
"""
from __future__ import annotations

# Éviter la sur-souscription : 1 thread BLAS/OpenMP par worker (le parallélisme est au niveau run).
import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import argparse  # noqa: E402
import math  # noqa: E402

import pandas as pd  # noqa: E402
from joblib import Parallel, delayed  # noqa: E402

from . import config  # noqa: E402
from .simulate import run  # noqa: E402

# Taux d'arrivée représentatifs pour le DOE (8 points -> courbes charge/réponse).
DOE_ARRIVAL_RATES = [2000, 4000, 8000, 12000, 16000, 20000, 26000, 32000]
DEFAULT_SEEDS = 25  # 5 stratégies × 8 taux × 25 seeds = 1000 runs


def _one(strategy: str, rate: float, seed: int, n_flows: int, topo: str) -> dict:
    m = run(strategy=strategy, arrival_rate=rate, n_flows=n_flows, topo=topo, seed=seed)
    m["seed"] = seed
    m["topo"] = topo
    m["n_flows"] = n_flows
    return m


def run_doe(
    strategies: list[str] = config.STRATEGIES,
    arrival_rates: list[float] = DOE_ARRIVAL_RATES,
    seeds: int = DEFAULT_SEEDS,
    n_flows: int = 6000,
    topo: str = config.DEFAULT_TOPOLOGY,
    n_jobs: int = -1,
) -> pd.DataFrame:
    """Exécute le plan d'expériences complet en parallèle."""
    tasks = [
        (s, r, seed)
        for s in strategies
        for r in arrival_rates
        for seed in range(seeds)
    ]
    results = Parallel(n_jobs=n_jobs, verbose=5)(
        delayed(_one)(s, r, seed, n_flows, topo) for s, r, seed in tasks
    )
    return pd.DataFrame(results)


def log_to_mlflow(df: pd.DataFrame, experiment_name: str = "sdn_ctrlml_edge") -> None:
    """Trace chaque run dans MLflow (backend SQLite, recommandé depuis MLflow 3)."""
    import mlflow

    mlflow.set_tracking_uri(f"sqlite:///{config.PROJECT_ROOT / 'mlflow.db'}")
    mlflow.set_experiment(experiment_name)

    param_cols = ["strategy", "arrival_rate", "seed", "topo", "n_flows"]
    metric_cols = [c for c in df.columns if c not in param_cols]
    for _, row in df.iterrows():
        with mlflow.start_run():
            mlflow.log_params({c: row[c] for c in param_cols})
            mlflow.log_metrics(
                {c: float(row[c]) for c in metric_cols if math.isfinite(row[c])}
            )


def main() -> None:
    p = argparse.ArgumentParser(description="Plan d'expériences SDN_CTRLML-Edge (DOE)")
    p.add_argument("--seeds", type=int, default=DEFAULT_SEEDS)
    p.add_argument("--n-flows", type=int, default=6000)
    p.add_argument("--topo", default=config.DEFAULT_TOPOLOGY)
    p.add_argument("--jobs", type=int, default=-1, help="workers joblib (-1 = tous les cœurs)")
    p.add_argument("--no-mlflow", action="store_true", help="ne pas tracer dans MLflow")
    args = p.parse_args()

    total = len(config.STRATEGIES) * len(DOE_ARRIVAL_RATES) * args.seeds
    print(f"DOE : {total} runs "
          f"({len(config.STRATEGIES)} stratégies × {len(DOE_ARRIVAL_RATES)} taux × {args.seeds} seeds), "
          f"n_flows={args.n_flows}")

    df = run_doe(seeds=args.seeds, n_flows=args.n_flows, topo=args.topo, n_jobs=args.jobs)

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = config.REPORTS_DIR / "experiments.csv"
    df.to_csv(out, index=False)
    print(f"\n{len(df)} runs -> {out}")

    if not args.no_mlflow:
        log_to_mlflow(df)
        print(f"MLflow : sqlite:///{config.PROJECT_ROOT / 'mlflow.db'}  (UI : make mlflow-ui)")
    print("Analyse : make analyze")


if __name__ == "__main__":
    main()
