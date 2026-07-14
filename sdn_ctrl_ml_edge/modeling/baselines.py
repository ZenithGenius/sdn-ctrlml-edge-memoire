"""Stratégies de référence (baselines) pour la comparaison du chapitre Résultats.

- onos      : binding statique — chaque switch lié au contrôleur le plus proche (fixe).
- sdnctrlml : prédécesseur (Aly et al.) — équilibrage sur la seule métrique CPU, sans EMA
              ni conscience de la latence (join-shortest-queue sur le CPU).
- rr        : round-robin sur les contrôleurs.
- random    : contrôleur aléatoire.
"""
from __future__ import annotations

import numpy as np

from .. import config
from .decision import Context, EdgeStrategy, Strategy


class OnosStatic(Strategy):
    name = "onos"

    def __init__(self):
        self._bind: dict[int, int] = {}

    def assign(self, src: int, ctx: Context) -> int:
        # Liaison maître fixe : calculée une fois, jamais migrée.
        if src not in self._bind:
            self._bind[src] = ctx.infra.nearest_controller(src)
        return self._bind[src]


class SdnCtrlMl(Strategy):
    """Équilibrage CPU seul : affecte au contrôleur le moins chargé (ignore la latence)."""
    name = "sdnctrlml"

    def assign(self, src: int, ctx: Context) -> int:
        return min(ctx.infra.controllers, key=lambda c: ctx.load.get(c, 0.0))


class RoundRobin(Strategy):
    name = "rr"

    def __init__(self):
        self._i = 0

    def assign(self, src: int, ctx: Context) -> int:
        controllers = ctx.infra.controllers
        c = controllers[self._i % len(controllers)]
        self._i += 1
        return c


class RandomAssign(Strategy):
    name = "random"

    def __init__(self, seed: int = config.RANDOM_SEED):
        self._rng = np.random.default_rng(seed)

    def assign(self, src: int, ctx: Context) -> int:
        return int(self._rng.choice(ctx.infra.controllers))


def make_strategy(name: str) -> Strategy:
    """Fabrique une stratégie par nom (cf. config.STRATEGIES)."""
    table = {
        "edge": EdgeStrategy,
        "sdnctrlml": SdnCtrlMl,
        "onos": OnosStatic,
        "rr": RoundRobin,
        "random": RandomAssign,
    }
    if name not in table:
        raise ValueError(f"Stratégie inconnue: {name!r} (choix: {list(table)})")
    return table[name]()
