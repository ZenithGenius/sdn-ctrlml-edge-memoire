#!/usr/bin/env bash
# Construit et lance le PoC d'émulation réelle SDN_CTRLML-Edge (Mininet + OVS + os-ken).
#
# Prérequis : Docker, et le module noyau openvswitch chargé sur l'hôte
#   (sudo modprobe openvswitch  ;  vérifier : lsmod | grep openvswitch).
# L'émulation OVS a besoin des droits root -> lancer avec sudo si docker l'exige.
#
# Usage :
#   sdn_ctrl_ml_edge/emulation/run.sh                 # rééquilibrage activé (proposé)
#   sdn_ctrl_ml_edge/emulation/run.sh --no-rebalance  # baseline (déséquilibre subi)
#   sdn_ctrl_ml_edge/emulation/run.sh --switches 8 --controllers 4
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMG=sdn-ctrlml-edge-emu

echo ">> Construction de l'image $IMG (première fois : ~quelques minutes)…"
docker build -t "$IMG" -f "$ROOT/sdn_ctrl_ml_edge/emulation/Dockerfile" "$ROOT"

echo ">> Vérification du datapath OVS (module noyau)…"
# /proc/modules plutôt que lsmod : toujours lisible, et lsmod (dans /sbin) n'est pas
# forcément dans le PATH d'un utilisateur non root.
if ! grep -q '^openvswitch ' /proc/modules 2>/dev/null; then
  echo "   module 'openvswitch' non chargé : tentative (sudo modprobe openvswitch)"
  sudo modprobe openvswitch || {
    echo "   ECHEC : chargez-le manuellement avec 'sudo modprobe openvswitch'"; exit 1; }
fi

echo ">> Lancement du PoC…"
# L'ENTRYPOINT de l'image démarre Open vSwitch puis lance un `bash` en ignorant la
# commande : on lui fournit donc le PoC sur son entrée standard. --privileged et le
# montage de /lib/modules donnent au conteneur l'accès au datapath du noyau hôte.
echo "cd /sdn && exec python3 -m sdn_ctrl_ml_edge.emulation.run_poc $*" \
  | docker run -i --privileged --rm -v /lib/modules:/lib/modules:ro "$IMG"
