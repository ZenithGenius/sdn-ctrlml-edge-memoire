"""Orchestration expérimentale : balaye stratégies × taux d'arrivée, agrège, trace.

Produit `reports/results.csv` + les figures dans `reports/figures/`. C'est le point
d'entrée qui alimente le chapitre « Résultats et commentaires ».
"""
from __future__ import annotations

import argparse

import pandas as pd

from . import config, plots
from .simulate import run


def sweep(
    strategies: list[str] = config.STRATEGIES,
    arrival_rates: list[float] = config.ARRIVAL_RATES,
    n_flows: int = 20000,
    topo: str = config.DEFAULT_TOPOLOGY,
    seed: int = config.RANDOM_SEED,
) -> pd.DataFrame:
    rows = []
    for strat in strategies:
        for rate in arrival_rates:
            rows.append(run(strategy=strat, arrival_rate=rate,
                            n_flows=n_flows, topo=topo, seed=seed))
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Évaluation comparative SDN_CTRLML-Edge")
    p.add_argument("--n-flows", type=int, default=20000)
    p.add_argument("--topo", default=config.DEFAULT_TOPOLOGY)
    p.add_argument("--seed", type=int, default=config.RANDOM_SEED)
    args = p.parse_args()

    df = sweep(n_flows=args.n_flows, topo=args.topo, seed=args.seed)

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = config.REPORTS_DIR / "results.csv"
    df.to_csv(csv_path, index=False)
    figs = plots.generate_all(df)

    # Synthèse : moyenne par stratégie sur tous les taux d'arrivée.
    summary = (df.groupby("strategy")[
        ["response_time_ms", "throughput_fps", "cpu_util_mean", "jain_fairness", "migrations"]
    ].mean().round(3).sort_values("jain_fairness", ascending=False))

    print("\n=== Synthèse (moyenne sur tous les taux d'arrivée) ===")
    print(summary.to_string())
    print(f"\nRésultats : {csv_path}")
    for f in figs:
        print(f"Figure    : {f}")


if __name__ == "__main__":
    main()
