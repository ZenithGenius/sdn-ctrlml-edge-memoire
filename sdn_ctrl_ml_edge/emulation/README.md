# PoC d'émulation réelle — SDN_CTRLML-Edge sur OpenFlow (Mininet + OVS + os-ken)

Ce dossier démontre que l'algorithme du mémoire fonctionne sur un **vrai réseau SDN
émulé** (switches Open vSwitch, contrôleurs parlant OpenFlow 1.3), pas seulement dans le
simulateur Python. C'est la réponse à « est-ce que ça marche vraiment, sur du réel ? ».

Tout tourne **dans Docker** et **sans Java** : le contrôleur est **os-ken**, le fork
maintenu de Ryu (Ryu ne s'installe plus sur Python 3.10+ ; Containernet reste un
sur-ensemble compatible mais son image officielle est bloquée en Python 3.6, trop vieux
pour un contrôleur maintenu — on bâtit donc sur l'image Mininet+OVS maintenue).

## Ce que ça montre

Trois contrôleurs SDN de capacités hétérogènes (Cloud/Fog/Edge). Au départ, **tous les
switches sont rattachés au contrôleur 0** : il sature (déséquilibre subi, comme un binding
statique de type ONOS). Le rééquilibreur lit la charge réelle (taux de Packet-In) de
chaque contrôleur, la classe avec **le clustering K-means++ multi-métriques du mémoire**
(`sdn_ctrl_ml_edge/modeling/clustering.py`, le même code que le chapitre III), puis agit
en deux temps via `ovs-vsctl`, comme les profils du mémoire :

1. **délestage** des contrôleurs *surchargés*, jusqu'à leur part équitable seulement ;
2. **remplissage** des contrôleurs *sous-utilisés*, sans quoi un contrôleur vidé le
   resterait indéfiniment et sa capacité serait perdue.

Résultat attendu : l'indice d'équité de Jain part bas (tout sur c0) et **remonte vers 1**.
En mode `--no-rebalance`, il reste bas : la comparaison rend la contribution visible sur
du vrai OpenFlow.

## Résultats mesurés (`reports/emulation/`)

| Réseau | Trafic | Jain* (pondéré) | Migrations une fois stabilisé |
|---|---|---|---|
| 3 contrôleurs / 6 switches | constant | 0,33 → **0,99** | 0 |
| 5 contrôleurs / 12 switches | constant | 0,20 → **0,935** | 0 |
| 4 contrôleurs / 10 switches | pics | 0,25 → **0,98** | 0 |
| 6 contrôleurs / 16 switches | pics | 0,17 → **0,95** (≈5 tours) | 0 |
| baseline `--no-rebalance` | constant | 0,33 → **0,33** (figé) | 0 |
| baseline `--no-rebalance` | pics | 0,25 → **0,25** (figé) | 0 |

## Composants

| Fichier | Rôle | Vérifié sans root ? |
|---|---|---|
| `controller_app.py` | Contrôleur os-ken : commutateur L2 + comptage Packet-In + REST `/load` | oui — écoute OpenFlow + `/load` OK |
| `run_controller.py` | Lanceur d'un contrôleur os-ken (équivalent `ryu-manager`) | oui |
| `rebalancer.py` | Décision (réutilise `clustering.classify`) + application `ovs-vsctl` | oui — `tests/test_emulation.py` |
| `run_poc.py` | Orchestrateur : lance les contrôleurs, la topo, le trafic, la mesure | non (Mininet = root) |
| `Dockerfile` / `run.sh` | Emballage image + lancement | — |

Ce qui a été **vérifié** hors-root : le contrôleur os-ken démarre, écoute l'OpenFlow et
sert `/load` (JSON) ; la décision de rééquilibrage (`pytest tests/test_emulation.py`)
délester un contrôleur saturé, ne migre rien sur un réseau équilibré, et normalise la
charge par la capacité. Seul le datapath OVS/Mininet, qui exige root, s'exécute chez vous.

## Prérequis

- **Docker** (vous êtes déjà dans le groupe `docker`).
- Le **module noyau `openvswitch`** chargé sur l'hôte :
  ```bash
  sudo modprobe openvswitch && lsmod | grep openvswitch
  ```
- Droits root pour le datapath OVS (via `sudo` si votre Docker l'exige).

## Lancer

```bash
# proposé : avec rééquilibrage
make emulate
#   ou : sdn_ctrl_ml_edge/emulation/run.sh

# baseline : sans rééquilibrage (déséquilibre subi, pour comparer)
make emulate ARGS=--no-rebalance

# variantes
sdn_ctrl_ml_edge/emulation/run.sh --switches 8 --controllers 4 --rounds 15
```

La première exécution construit l'image (quelques minutes). Chaque tour affiche l'indice
de Jain et les charges Packet-In par contrôleur.

## Adaptation du seuil de surcharge (à savoir pour la soutenance)

Le **clustering est identique** à celui du mémoire (`classify`, K-means++ multi-métriques).
Seul le **critère de surcharge** est exprimé sur l'échelle de l'émulation :

| | Simulateur (chapitre III) | Émulation (ce PoC) |
|---|---|---|
| Charge mesurée | utilisation CPU absolue [0,1] | Packet-In/s par contrôleur |
| Critère | `cpu ≥ 0.70` (`config.OVERLOAD_CPU`) | `≥ 1,5 ×` sa part équitable |

Pourquoi : en émulation les débits absolus (quelques dizaines de Packet-In/s) sont sans
commune mesure avec les capacités du modèle (milliers de req/s) ; un seuil absolu ne se
déclencherait jamais. Le critère relatif, lui, est **indépendant de l'échelle** et tient
compte de l'hétérogénéité : la part équitable d'un contrôleur est proportionnelle à sa
capacité, donc un petit contrôleur Edge est déclaré surchargé plus tôt qu'un Cloud.

Les règles apprises expirent (`hard_timeout = 2 s`) pour que le trafic regénère des
Packet-In : sans cela la charge tomberait à zéro dès les flux appris, et il n'y aurait
plus rien à mesurer ni à rééquilibrer. Un `idle_timeout` ne conviendrait pas : un flux
continu ne devient jamais inactif, donc la règle ne s'effacerait jamais.

**Lissage EMA : mesuré, sans bénéfice ici.** Les délestages se décident sur la charge
*lissée*, la capacité oisive sur la charge *brute* (l'EMA garde longtemps la trace d'un
pic passé et retarderait la récupération). Mais la comparaison directe, à configuration
identique (6 contrôleurs / 16 switches / pics), ne montre **aucun gain** :

| | Convergence | Jain* stabilisé | Migrations totales |
|---|---|---|---|
| avec EMA (`--alpha 0.4`) | tour 5 | 0,92 à 0,99 | 21 |
| sans EMA (`--no-ema`) | tour 4 | 0,98 stable | 21 |

Sans lissage, la convergence est même légèrement plus rapide et plus stable. Ce résultat
**confirme l'ablation menée sur le simulateur** : l'EMA n'est pas le moteur des gains.
L'option reste disponible (`--alpha`) et le traitement asymétrique existe pour qu'elle
soit au moins inoffensive, mais elle ne doit pas être présentée comme un apport mesuré.

## Honnêteté / limites

- Ce PoC est **qualitatif** : il prouve que l'algorithme s'exécute et rééquilibre sur du
  vrai OpenFlow. Les **résultats quantitatifs** du mémoire (1000 exécutions, IC95,
  Wilcoxon) restent produits par le simulateur, plus adapté à un plan d'expériences.
- La latence entre nœuds est ici uniforme (émulation locale) : la cible d'un switch
  déplacé est choisie par la charge. Le simulateur, lui, exploite la latence géographique
  réelle (Topology Zoo).
- Ce bac à sable de développement ne peut pas exécuter le datapath OVS (noyau sans module
  `openvswitch` accessible dans un conteneur) ; le PoC se lance sur une machine hôte.
