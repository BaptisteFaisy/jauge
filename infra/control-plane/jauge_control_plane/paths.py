"""Résolution des chemins d'actifs (assets) de l'application CDK.

Les chemins d'actifs de `Code.from_asset` sont résolus par le CDK relativement
au **répertoire courant** du processus de synthèse, ce qui diffère entre
`cdk synth` (lancé depuis `infra/control-plane`) et les tests pytest (lancés
depuis la racine du dépôt). On ancre donc les chemins sur l'emplacement de ce
fichier pour que la synthèse donne le même résultat partout.
"""

from __future__ import annotations

from pathlib import Path

# infra/control-plane/jauge_control_plane/paths.py -> racine du dépôt
RACINE_APPLICATION = Path(__file__).resolve().parent.parent
RACINE_DEPOT = RACINE_APPLICATION.parent.parent


def chemin_asset(*parties: str) -> str:
    """Chemin absolu d'un actif, ancré sur la racine du dépôt."""
    return str(RACINE_DEPOT.joinpath(*parties))


def chemin_application(*parties: str) -> str:
    """Chemin absolu d'un actif, ancré sur `infra/control-plane`."""
    return str(RACINE_APPLICATION.joinpath(*parties))
