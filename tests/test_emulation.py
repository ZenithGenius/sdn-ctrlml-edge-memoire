"""Vérifie la décision de rééquilibrage de l'émulation (fonction pure, sans root ni Ryu)."""
from sdn_ctrl_ml_edge.emulation.rebalancer import plan_reassignments, controller_vectors

# 3 contrôleurs, capacités hétérogènes Cloud/Fog/Edge.
CAP = {0: 6000.0, 1: 3500.0, 2: 1800.0}


def _loads(rates, flows):
    return {c: {"packetin_rate_total": rates[c], "flows_total": flows[c]} for c in rates}


def test_overloaded_controller_is_relieved():
    # Contrôleur 2 (Edge, petite capacité) saturé ; 0 et 1 quasi inactifs.
    loads = _loads({0: 100.0, 1: 120.0, 2: 1700.0}, {0: 5, 1: 6, 2: 40})
    assignment = {10: 2, 11: 2, 12: 0, 13: 1}          # switches 10,11 sur le contrôleur saturé
    switch_load = {10: 900.0, 11: 800.0, 12: 100.0, 13: 120.0}
    moves, decision = plan_reassignments(loads, assignment, switch_load, CAP)
    assert moves, "le contrôleur saturé aurait dû être délesté"
    assert set(moves) <= {10, 11}
    assert all(dst in (0, 1) for dst in moves.values())
    assert decision["profile"]["2"] == "surchargé"
    assert "cluster" in decision and "k" in decision


def test_balanced_network_no_migration():
    # Charge répartie AU PRORATA des capacités (6000/3500/1800) : chaque contrôleur
    # porte exactement sa part, donc aucune surcharge et aucune migration.
    loads = _loads({0: 600.0, 1: 350.0, 2: 180.0}, {0: 10, 1: 9, 2: 11})
    assignment = {10: 0, 11: 1, 12: 2}
    switch_load = {10: 600.0, 11: 350.0, 12: 180.0}
    moves, decision = plan_reassignments(loads, assignment, switch_load, CAP)
    assert moves == {}
    assert set(decision["profile"].values()) <= {"normal", "sous-utilisé", "surchargé"}


def test_small_controller_overloaded_despite_lower_raw_rate():
    # Débits bruts proches, mais le contrôleur Edge (capacité 1800) porte bien plus
    # que sa part : l'hétérogénéité doit le faire ressortir comme surchargé.
    loads = _loads({0: 300.0, 1: 280.0, 2: 900.0}, {0: 10, 1: 9, 2: 30})
    assignment = {10: 0, 11: 1, 12: 2, 13: 2}
    switch_load = {10: 300.0, 11: 280.0, 12: 500.0, 13: 400.0}
    moves, _ = plan_reassignments(loads, assignment, switch_load, CAP)
    assert moves, "le contrôleur Edge surchargé aurait dû être délesté"
    assert set(moves) <= {12, 13}
    assert all(dst in (0, 1) for dst in moves.values())


def test_does_not_evacuate_overloaded_controller():
    # c0 porte tout ; il doit être ramené vers sa part, PAS vidé entièrement
    # (l'évacuer créerait le déséquilibre inverse et ferait osciller le système).
    loads = _loads({0: 90.0, 1: 0.0, 2: 0.0}, {0: 30, 1: 0, 2: 0})
    assignment = {s: 0 for s in range(1, 7)}
    switch_load = {1: 15.0, 2: 15.0, 3: 15.0, 4: 15.0, 5: 15.0, 6: 15.0}
    moves, _ = plan_reassignments(loads, assignment, switch_load, CAP)
    assert moves, "c0 est surchargé, il doit être délesté"
    assert len(moves) < 6, "c0 ne doit pas être entièrement évacué"
    remaining = 90.0 - sum(switch_load[s] for s in moves)
    assert remaining > 0


def test_idle_controller_is_refilled():
    # c0 (le plus gros) est OISIF alors que les autres travaillent, sans qu'aucun ne
    # soit surchargé : sa capacité est perdue, l'algorithme doit lui ramener des flux.
    loads = _loads({0: 0.0, 1: 200.0, 2: 200.0}, {0: 0, 1: 20, 2: 20})
    assignment = {10: 1, 11: 1, 12: 2, 13: 2}
    switch_load = {10: 100.0, 11: 100.0, 12: 100.0, 13: 100.0}
    moves, _ = plan_reassignments(loads, assignment, switch_load, CAP)
    assert any(dst == 0 for dst in moves.values()), \
        "le contrôleur oisif doit être réalimenté (profil « sous-utilisé »)"


def test_vectors_normalise_by_capacity():
    # Même débit brut, mais l'Edge (capacité faible) doit ressortir plus chargé (cpu).
    loads = _loads({0: 1800.0, 2: 1800.0}, {0: 10, 2: 10})
    cap = {0: 6000.0, 2: 1800.0}
    controllers, vecs = controller_vectors(loads, cap)
    cpu = dict(zip(controllers, vecs[:, 0]))
    assert cpu[2] > cpu[0]                                # 1800/1800=1.0 > 1800/6000=0.3
