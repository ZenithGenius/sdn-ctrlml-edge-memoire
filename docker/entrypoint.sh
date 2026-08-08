#!/usr/bin/env bash
# Démarre Open vSwitch si nécessaire, puis exécute la commande demandée.
#
# L'ENTRYPOINT de l'image de base lance un shell interactif et ignore la commande,
# ce qui empêche toute exécution non interactive (docker compose, CI). On le
# remplace ici.
#
# START_OVS=0 saute le démarrage d'Open vSwitch : indispensable pour les usages
# sans privilèges (tests, simulateur), où le démarrage du service resterait
# suspendu faute d'accès au datapath du noyau. Le délai maximal évite qu'un
# environnement inhabituel ne fige le conteneur.
set -e

if [ "${START_OVS:-1}" = "1" ]; then
  timeout 40 service openvswitch-switch start >/dev/null 2>&1 || true
  timeout 10 ovs-vsctl set-manager ptcp:6640 >/dev/null 2>&1 || true
  trap 'timeout 20 service openvswitch-switch stop >/dev/null 2>&1 || true' EXIT
fi

if [ "$#" -eq 0 ]; then
  exec bash
fi
exec "$@"
