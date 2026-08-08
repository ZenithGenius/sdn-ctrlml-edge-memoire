.PHONY: help venv requirements test simulate evaluate experiment analyze flowviz emulate compose-emulate mlflow-ui clean

PY := .venv/bin/python
PIP := .venv/bin/pip

help:
	@echo "venv         - crée le venv (.venv) avec les paquets système réutilisés"
	@echo "requirements - installe les dépendances"
	@echo "test         - lance les tests de vérification"
	@echo "simulate     - une simulation (STRATEGY=edge RATE=16000)"
	@echo "evaluate     - balayage déterministe rapide + figures (reports/figures/)"
	@echo "experiment   - plan d'expériences ~1000 runs (multi-seeds) + MLflow (SEEDS=25)"
	@echo "analyze      - analyse statistique (IC95 + Wilcoxon) + figures + rapport"
	@echo "flowviz      - génère reports/flux_sdn.html (flux en direct, à ouvrir au navigateur)"
	@echo "emulate      - PoC réel sur OVS/OpenFlow (Mininet+os-ken ; Docker requis)"
	@echo "compose-emulate - idem via docker compose (marche aussi sous Windows)"
	@echo "mlflow-ui    - ouvre l'UI MLflow sur http://127.0.0.1:5000"
	@echo "clean        - supprime caches et artefacts"

venv:
	python3 -m venv --system-site-packages .venv

requirements:
	$(PIP) install -q -r requirements.txt

test:
	$(PY) -m pytest -q tests/

STRATEGY ?= edge
RATE ?= 16000
simulate:
	$(PY) -m sdn_ctrl_ml_edge.simulate --strategy $(STRATEGY) --arrival-rate $(RATE)

evaluate:
	$(PY) -m sdn_ctrl_ml_edge.evaluate

SEEDS ?= 25
experiment:
	$(PY) -m sdn_ctrl_ml_edge.experiment --seeds $(SEEDS)

analyze:
	$(PY) -m sdn_ctrl_ml_edge.analysis

RATE ?= 16000
flowviz:
	$(PY) -m sdn_ctrl_ml_edge.flowviz --rate $(RATE)
	@echo "Ouvrez reports/flux_sdn.html dans un navigateur."

ARGS ?=
emulate:
	sdn_ctrl_ml_edge/emulation/run.sh $(ARGS)

# Équivalent multiplateforme (Windows compris), sans make ni bash :
#   docker compose -f docker/docker-compose.yml run --rm emulate
#   docker compose -f docker/docker-compose.yml run --rm baseline
#   docker compose -f docker/docker-compose.yml run --rm sim
compose-emulate:
	docker compose -f docker/docker-compose.yml run --rm emulate

mlflow-ui:
	.venv/bin/mlflow ui --backend-store-uri sqlite:///mlflow.db

clean:
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + ; rm -rf .pytest_cache
