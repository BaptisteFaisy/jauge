"""Port d'accès à la table d'état DynamoDB, et son adaptateur boto3.

`DynamoDbStore` ne parle jamais à boto3 directement : il passe par ce port à
cinq méthodes. Deux raisons :

1. les tests injectent un double en mémoire (`tests/store/fake_dynamo.py`) —
   aucun appel réseau, aucune crédentiale, conformément aux règles du dépôt ;
2. la construction des expressions de clés (`Key(...).eq(...).begins_with(...)`)
   et la pagination restent confinées dans l'adaptateur : le store, lui, reste
   lisible et testable.

L'adaptateur traduit toute erreur boto3 en `ServiceIndisponibleError` : une
panne DynamoDB remonte en 502 explicite, jamais en trace brute dans l'API.
"""

from __future__ import annotations

from typing import Any, Protocol

from jauge.common.errors import ServiceIndisponibleError


class TableEtat(Protocol):
    """Contrat minimal d'une table d'état à deux clés (`PK`, `SK`)."""

    def ecrire(self, item: dict[str, Any]) -> None:
        """Écrit ou remplace un item — opération idempotente."""
        ...

    def lire(self, partition: str, cle: str) -> dict[str, Any] | None:
        """Lit un item par ses deux clés, ou `None` s'il est absent."""
        ...

    def requeter(self, partition: str, prefixe_cle: str | None = None) -> list[dict[str, Any]]:
        """Items d'une partition, éventuellement filtrés par préfixe de clé de tri."""
        ...

    def requeter_index(self, cle_index: str) -> list[dict[str, Any]]:
        """Items d'une partition de l'index `par-type` (projection : clés seules)."""
        ...

    def supprimer(self, partition: str, cle: str) -> None:
        """Supprime un item (idempotent)."""
        ...


class TableBoto3:
    """Adaptateur boto3 du port `TableEtat` (ressource `Table` injectée)."""

    def __init__(self, table: Any) -> None:
        self._table = table

    @classmethod
    def depuis_environnement(cls, nom_table: str, region: str | None = None) -> TableBoto3:
        """Construit la ressource boto3 — seul chemin qui touche AWS."""
        import boto3

        arguments = {"region_name": region} if region else {}
        ressource = boto3.resource("dynamodb", **arguments)
        return cls(ressource.Table(nom_table))

    def ecrire(self, item: dict[str, Any]) -> None:
        """`put_item` : écrire deux fois le même item ne duplique rien."""
        self._appeler("put_item", Item=item)

    def lire(self, partition: str, cle: str) -> dict[str, Any] | None:
        """`get_item` : `None` quand l'item est absent."""
        reponse = self._appeler("get_item", Key={"PK": partition, "SK": cle})
        item = reponse.get("Item")
        return dict(item) if item else None

    def requeter(self, partition: str, prefixe_cle: str | None = None) -> list[dict[str, Any]]:
        """`query` paginé sur la partition, avec préfixe de clé de tri si fourni."""
        from boto3.dynamodb.conditions import Key

        condition = Key("PK").eq(partition)
        if prefixe_cle:
            condition = condition & Key("SK").begins_with(prefixe_cle)
        return self._paginer({"KeyConditionExpression": condition})

    def requeter_index(self, cle_index: str) -> list[dict[str, Any]]:
        """`query` paginé sur l'index `par-type` (clés seules projetées)."""
        from boto3.dynamodb.conditions import Key

        return self._paginer(
            {
                "IndexName": "par-type",
                "KeyConditionExpression": Key("GSI1PK").eq(cle_index),
            }
        )

    def supprimer(self, partition: str, cle: str) -> None:
        """`delete_item` : supprimer un item absent ne fait rien."""
        self._appeler("delete_item", Key={"PK": partition, "SK": cle})

    def _paginer(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        """Accumule les pages d'un `query` jusqu'à épuisement du jeton."""
        items: list[dict[str, Any]] = []
        parametres = dict(arguments)
        while True:
            reponse = self._appeler("query", **parametres)
            items.extend(dict(item) for item in reponse.get("Items", []))
            jeton = reponse.get("LastEvaluatedKey")
            if not jeton:
                return items
            parametres["ExclusiveStartKey"] = jeton

    def _appeler(self, operation: str, **arguments: Any) -> dict[str, Any]:
        """Exécute l'opération et traduit les erreurs boto3 (jamais brutes)."""
        try:
            return dict(getattr(self._table, operation)(**arguments) or {})
        except Exception as erreur:  # noqa: BLE001 - traduite en erreur métier
            raise ServiceIndisponibleError(
                "DynamoDB indisponible ou accès refusé",
                operation=operation,
                erreur=type(erreur).__name__,
            ) from erreur
