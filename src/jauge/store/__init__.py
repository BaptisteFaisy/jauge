"""Persistence : contrat `Store`, implémentations mémoire et DynamoDB.

Deux implémentations du même contrat :

- `MemoryStore` — dictionnaires Python : tests, démo hors ligne, portail local ;
- `DynamoDbStore` — la table d'état unique créée par le CDK du plan de contrôle
  (`infra/control-plane/`), avec le client dans la clé de partition.

`creer_store()` choisit l'implémentation d'après l'environnement, pour que le
point d'entrée ASGI (`uvicorn jauge.api.app:app`) serve l'un ou l'autre sans
modification de code :

| Variable | Valeur | Effet |
|---|---|---|
| `JAUGE_STORE` | `memory` (défaut) | store en mémoire |
| `JAUGE_STORE` | `dynamodb` | store DynamoDB |
| `JAUGE_DYNAMO_TABLE` | nom de la table | requis si `JAUGE_STORE=dynamodb` |
| `JAUGE_STATE_TABLE` | nom de la table | accepté comme synonyme — c'est le nom que pose le CDK (`constructs/api.py`) |
| `JAUGE_DYNAMO_REGION` | région AWS | optionnel (sinon région ambiante) |
"""

from __future__ import annotations

import os

from jauge.common.errors import ServiceIndisponibleError
from jauge.store.dynamodb import DynamoDbStore
from jauge.store.memory import MemoryStore
from jauge.store.protocol import Store
from jauge.store.table import TableBoto3, TableEtat

#: Valeurs de `JAUGE_STORE` acceptées pour DynamoDB.
VALEURS_DYNAMODB = frozenset({"dynamodb", "dynamo"})

__all__ = [
    "DynamoDbStore",
    "MemoryStore",
    "Store",
    "TableBoto3",
    "TableEtat",
    "creer_store",
]


def creer_store() -> Store:
    """Construit le store demandé par l'environnement (mémoire par défaut).

    L'import de boto3 est différé dans `TableBoto3.depuis_environnement` : en
    mode mémoire, aucune dépendance AWS n'est chargée.
    """
    choix = os.getenv("JAUGE_STORE", "memory").strip().lower()
    if choix not in VALEURS_DYNAMODB:
        return MemoryStore()
    # `JAUGE_STATE_TABLE` est le nom posé par le CDK dans l'environnement de la
    # Lambda ; `JAUGE_DYNAMO_TABLE` est accepté pour un déploiement à la main.
    nom_table = (
        os.getenv("JAUGE_DYNAMO_TABLE", "").strip() or os.getenv("JAUGE_STATE_TABLE", "").strip()
    )
    if not nom_table:
        # 502 et non 422 : c'est la configuration du déploiement qui est en
        # cause, pas la requête de l'appelant.
        raise ServiceIndisponibleError(
            "JAUGE_DYNAMO_TABLE (ou JAUGE_STATE_TABLE) est requis quand JAUGE_STORE=dynamodb",
            variable="JAUGE_DYNAMO_TABLE",
        )
    region = os.getenv("JAUGE_DYNAMO_REGION", "").strip() or None
    return DynamoDbStore(TableBoto3.depuis_environnement(nom_table, region))
