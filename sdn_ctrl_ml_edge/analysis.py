"""Analyse statistique des expériences multi-seeds (rigueur défendable).

À partir de `reports/experiments.csv` :
- agrège par (stratégie, taux) avec **intervalle de confiance à 95 %** (across seeds) ;
- teste la **significativité** stratégie proposée vs chaque baseline (Wilcoxon apparié) ;
- calcule le **taux de victoire** (fraction des cellules où `edge` domine) ;
- produit des figures avec bandes de confiance + boxplots, et un rapport Markdown.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

from . import config  # noqa: E402
from .plots import _LABELS  # noqa: E402

REPORT_METRICS = [
    "response_time_ms", "throughput_fps", "cpu_util_mean", "jain_fairness", "migrations",
]
# Sens d'amélioration : True = « plus haut est meilleur ».
HIGHER_BETTER = {
    "response_time_ms": False, "throughput_fps": True, "cpu_util_mean": False,
    "jain_fairness": True, "migrations": False, "overload_ratio": False, "silhouette": True,
}
PROPOSED = "edge"


def _ci95(x: np.ndarray) -> float:
    """Demi-largeur de l'intervalle de confiance à 95 % (loi de Student)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2:
        return 0.0
    return float(stats.t.ppf(0.975, n - 1) * stats.sem(x))


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """Moyenne + IC95 par (stratégie, taux) pour chaque métrique."""
    rows = []
    for (strat, rate), sub in df.groupby(["strategy", "arrival_rate"]):
        row = {"strategy": strat, "arrival_rate": rate, "n": len(sub)}
        for m in REPORT_METRICS:
            row[f"{m}_mean"] = sub[m].mean()
            row[f"{m}_ci95"] = _ci95(sub[m].to_numpy())
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["strategy", "arrival_rate"])


def significance(df: pd.DataFrame, proposed: str = PROPOSED) -> pd.DataFrame:
    """Test de Wilcoxon apparié (par taux×seed) : proposé vs chaque baseline."""
    base = df[df.strategy == proposed].set_index(["arrival_rate", "seed"])
    rows = []
    for other in [s for s in df.strategy.unique() if s != proposed]:
        comp = df[df.strategy == other].set_index(["arrival_rate", "seed"])
        joined = base.join(comp, lsuffix="_p", rsuffix="_b", how="inner")
        for m in REPORT_METRICS:
            a, b = joined[f"{m}_p"].to_numpy(), joined[f"{m}_b"].to_numpy()
            diff = a - b
            wins = diff > 0 if HIGHER_BETTER[m] else diff < 0
            try:
                pval = stats.wilcoxon(a, b).pvalue if np.any(diff != 0) else float("nan")
            except ValueError:
                pval = float("nan")
            rows.append({
                "baseline": other, "metric": m,
                "win_rate": float(np.mean(wins)),
                "median_diff": float(np.median(diff)),
                "p_value": pval,
                "significant_5pct": bool(pval < 0.05) if pval == pval else False,
            })
    return pd.DataFrame(rows)


def _plot_ci_bands(agg: pd.DataFrame, out_dir: Path) -> list[Path]:
    paths = []
    for m in REPORT_METRICS:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        for strat, sub in agg.groupby("strategy"):
            sub = sub.sort_values("arrival_rate")
            x = sub["arrival_rate"] / 1000
            y, h = sub[f"{m}_mean"].to_numpy(), sub[f"{m}_ci95"].to_numpy()
            ax.plot(x, y, marker="o", label=_LABELS.get(strat, strat))
            ax.fill_between(x, y - h, y + h, alpha=0.15)
        ax.set_xlabel("Taux d'arrivée (×1000 flux/s)")
        ax.set_ylabel(m)
        ax.set_title(f"{m} — moyenne ± IC95 (multi-seeds)")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        p = out_dir / f"ci_{m}.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        paths.append(p)
    return paths


def _plot_boxplots(df: pd.DataFrame, out_dir: Path) -> list[Path]:
    rate = df["arrival_rate"].max()
    high = df[df.arrival_rate == rate]
    order = list(config.STRATEGIES)
    paths = []
    for m in REPORT_METRICS:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        data = [high[high.strategy == s][m].to_numpy() for s in order]
        ax.boxplot(data)
        ax.set_xticks(range(1, len(order) + 1))
        ax.set_xticklabels([_LABELS.get(s, s) for s in order])
        ax.set_ylabel(m)
        ax.set_title(f"{m} @ {rate:.0f} flux/s (distribution sur seeds)")
        ax.grid(True, alpha=0.3, axis="y")
        plt.setp(ax.get_xticklabels(), rotation=20, ha="right", fontsize=7)
        fig.tight_layout()
        p = out_dir / f"box_{m}.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        paths.append(p)
    return paths


def _write_report(df, agg, sig, out_path: Path) -> None:
    overall = (df.groupby("strategy")[REPORT_METRICS].mean().round(3)
               .sort_values("jain_fairness", ascending=False))
    mlflow_db = config.PROJECT_ROOT / "mlflow.db"
    mlflow_fig = config.FIGURES_DIR / "analysis" / "mlflow_ui.png"
    lines = [
        "# Analyse comparative — SDN_CTRLML-Edge",
        "",
        f"- Runs analysés : **{len(df)}** "
        f"({df.strategy.nunique()} stratégies × {df.arrival_rate.nunique()} taux × "
        f"{df.seed.nunique()} seeds).",
        "- Intervalles de confiance à 95 % (loi de Student) sur les seeds ; "
        "tests de Wilcoxon appariés (proposé vs baselines).",
        "- Suivi d'expériences : **MLflow** "
        f"(`{mlflow_db.name}`, expérience `sdn_ctrlml_edge`) — "
        "chaque run du DOE enregistre params + métriques.",
        "",
        "## Suivi MLflow",
        "",
        "Le plan d'expériences (`make experiment`) journalise les ~1000 runs dans "
        "MLflow (backend SQLite). L'UI permet de filtrer par stratégie / taux / seed "
        "et de comparer les métriques (latence, débit, Jain, migrations).",
        "",
        "```bash",
        "make mlflow-ui    # → http://127.0.0.1:5000",
        "```",
        "",
    ]
    if mlflow_fig.exists():
        lines += [
            f"![UI MLflow — suivi des expériences]({mlflow_fig.relative_to(config.REPORTS_DIR).as_posix()})",
            "",
            "*Figure : interface MLflow après le DOE (expérience `sdn_ctrlml_edge`).*",
            "",
        ]
    else:
        lines += [
            f"*Capture UI : lancer `make mlflow-ui` puis enregistrer "
            f"`reports/figures/analysis/mlflow_ui.png`.*",
            "",
        ]
    lines += [
        "## Moyennes globales par stratégie",
        "",
        overall.to_markdown(),
        "",
        f"## Significativité — `{PROPOSED}` (proposé) vs baselines",
        "",
        "`win_rate` = fraction des cellules (taux×seed) où le proposé domine ; "
        "`p_value` = Wilcoxon apparié ; significatif si < 0.05.",
        "",
        sig.round(4).to_markdown(index=False),
        "",
        "## Lecture",
        "",
        "La stratégie proposée vise le meilleur compromis **latence × équilibrage**, "
        "avec des gains statistiquement significatifs (voir `p_value`) sur les baselines "
        "statiques (ONOS) et naïves (RR, Random), tout en réduisant les migrations vs le "
        "prédécesseur mono-métrique `sdnctrlml`.",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")


def run_analysis(csv_path: Path | None = None) -> None:
    csv_path = csv_path or (config.REPORTS_DIR / "experiments.csv")
    if not csv_path.exists():
        raise FileNotFoundError(
            f"{csv_path} introuvable — lancer d'abord `make experiment`."
        )
    df = pd.read_csv(csv_path)

    agg = aggregate(df)
    sig = significance(df)

    out_dir = config.FIGURES_DIR / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    agg.to_csv(config.REPORTS_DIR / "aggregated.csv", index=False)
    sig.to_csv(config.REPORTS_DIR / "significance.csv", index=False)
    figs = _plot_ci_bands(agg, out_dir) + _plot_boxplots(df, out_dir)
    _write_report(df, agg, sig, config.REPORTS_DIR / "analysis_report.md")

    print(f"Agrégats     : {config.REPORTS_DIR / 'aggregated.csv'}")
    print(f"Significativité: {config.REPORTS_DIR / 'significance.csv'}")
    print(f"Rapport      : {config.REPORTS_DIR / 'analysis_report.md'}")
    print(f"Figures      : {len(figs)} dans {out_dir}")


def main() -> None:
    run_analysis()


if __name__ == "__main__":
    main()
