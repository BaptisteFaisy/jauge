#!/usr/bin/env bash
# ci-local.sh — équivalent local des quatre jobs de la CI (lint, limits, test, iac).
# À lancer avant toute PR : la CI self-hosted ne pardonne pas un formatage oublié.
set -euo pipefail
cd "$(dirname "$0")/.."

export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null 2>&1 || { echo "ERREUR : uv introuvable (https://docs.astral.sh/uv/)"; exit 1; }

echo "==> uv sync --all-groups"
uv sync --frozen --all-groups

echo "==> lint (ruff check + format)"
uv run ruff check .
uv run ruff format --check .

echo "==> limits (fichiers < 500 lignes, fonctions < 50 lignes)"
python3 scripts/check_limits.py

echo "==> test (hors infra)"
uv run pytest -q -m 'not infra and not slow'

echo "==> iac (cfn-lint + tests CDK)"
shopt -s nullglob globstar
fichiers=(infra/**/*.yaml infra/**/*.yml infra/**/*.json)
if [ ${#fichiers[@]} -gt 0 ]; then
  uv run cfn-lint "${fichiers[@]}"
else
  echo "    (aucun gabarit dans infra/)"
fi
if [ -d tests/infra ]; then
  uv run pytest -q -m infra tests/infra
else
  echo "    (aucun test infra pour l'instant)"
fi

echo "==> CI locale verte"
