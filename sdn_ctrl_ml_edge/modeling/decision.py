"""Couche Décisionnelle — stratégie proposée SDN_CTRLML-Edge.

Exploite le clustering EMA+K-means++ : un flux est affecté au contrôleur le plus
proche (latence) parmi ceux qui ne sont PAS dans le cluster surchargé. C'est ce qui
distingue la proposition des baselines : conscience simultanée de la charge (multi-
métriques, lissée) ET de la latence géographique.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import config
from ..dataset import Infrastructure
from .clustering import ClusterResult


@dataclass
class Context:
    """État partagé, mis à jour par le simulateur et lu par les stratégies."""
    infra: Infrastructure
    load: dict[int, float] = field(default_factory=dict)   # utilisation CPU courante par contrôleur
    cluster: ClusterResult | None = None                   # dernier résultat de clustering (None avant 1er)

    def ctrl_index(self, controller: int) -> int:
        return self.infra.controllers.index(controller)


class Strategy:
    name = "base"

    def assign(self, src: int, ctx: Context) -> int:  # pragma: no cover - interface
        raise NotImplementedError


class EdgeStrategy(Strategy):
    """SDN_CTRLML-Edge : contrôleur le plus proche hors cluster surchargé."""
    name = "edge"

    def assign(self, src: int, ctx: Context) -> int:
        controllers = ctx.infra.controllers
        lat = ctx.infra.latency[src]

        candidates = controllers
        if ctx.cluster is not None and ctx.cluster.overloaded:
            kept = [c for c in controllers
                    if ctx.cluster.labels[ctx.ctrl_index(c)] not in ctx.cluster.overloaded]
            candidates = kept or controllers  # si tout est surchargé, ne pas s'exclure

        # « Le plus proche ET le moins chargé » (objectif 6 du mémoire), en deux régimes :
        # - régime normal : parmi les contrôleurs individuellement sains (< seuil de
        #   surcharge, charge tenue à jour au fil des affectations), le plus proche en
        #   latence (arrondie à la ms — en deçà, du bruit), départagé par la charge ;
        # - régime saturé (aucun contrôleur sain) : délestage pur vers le moins chargé —
        #   quand tout est chaud, l'attente domine la latence réseau, s'obstiner sur le
        #   plus proche re-concentrerait la charge.
        healthy = [c for c in candidates if ctx.load.get(c, 0.0) < config.OVERLOAD_CPU]
        if healthy:
            return min(healthy, key=lambda c: (round(lat[c]), ctx.load.get(c, 0.0)))
        return min(candidates, key=lambda c: ctx.load.get(c, 0.0))
