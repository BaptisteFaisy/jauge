"""Table des tarifs embarquée de la démonstration (EF-ATT-04, §23 Q3).

`table_demo()` sert aux tests et au parcours de démonstration : elle charge le
fichier `data/pricing/bedrock-eu-west-1.json` du dépôt, dont les montants sont
des **valeurs de démonstration** (voir le champ `_avertissement` du fichier) et
non les tarifs officiels d'Amazon.

Le fichier est cherché, dans l'ordre :
1. le chemin de la variable d'environnement `JAUGE_TABLE_TARIFS` ;
2. `data/pricing/` à la racine du dépôt (cas d'un clone ou d'un `uv sync`) ;
3. `data/pricing/` à côté de ce module (cas d'une roue qui embarquerait la table).

Le point 3 est le seul qui survive à une installation : tant que la roue
n'embarque pas `data/pricing/` (`pyproject.toml` est verrouillé), `table_demo()`
ne fonctionne que depuis un clone — besoin de socle signalé dans la PR.
"""

from __future__ import annotations

import os
from pathlib import Path

from jauge.common.errors import ValidationError
from jauge.engine.calculator import TableTarifs
from jauge.pricing_data.loader import charger_table

#: Nom du fichier de démonstration (une région, trois modèles, deux niveaux).
NOM_FICHIER_DEMO = "bedrock-eu-west-1.json"

#: Variable d'environnement qui force un autre fichier de tarifs.
VARIABLE_ENV = "JAUGE_TABLE_TARIFS"


def chemins_candidats() -> list[Path]:
    """Emplacements essayés pour trouver la table embarquée."""
    candidats: list[Path] = []
    force = os.environ.get(VARIABLE_ENV)
    if force:
        candidats.append(Path(force).expanduser())
    ici = Path(__file__).resolve()
    racine_depot = ici.parents[3]  # src/jauge/pricing_data/seed.py -> racine
    candidats.append(racine_depot / "data" / "pricing" / NOM_FICHIER_DEMO)
    candidats.append(ici.parent / "data" / "pricing" / NOM_FICHIER_DEMO)
    return candidats


def chemin_table_demo() -> Path:
    """Chemin du premier fichier de tarifs existant, sinon `ValidationError`."""
    candidats = chemins_candidats()
    for candidat in candidats:
        if candidat.is_file():
            return candidat
    essais = ", ".join(str(candidat) for candidat in candidats)
    raise ValidationError(
        f"table de tarifs de démonstration introuvable ({NOM_FICHIER_DEMO}) ; "
        f"chemins essayés : {essais} — définir {VARIABLE_ENV}",
        variable=VARIABLE_ENV,
    )


def table_demo() -> TableTarifs:
    """Table des tarifs de démonstration, chargée et validée."""
    return charger_table(chemin_table_demo())
