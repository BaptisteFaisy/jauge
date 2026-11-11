"""Double en mémoire du port `TableEtat` — aucun réseau, aucune crédentiale.

Il imite la table DynamoDB sur les points qui comptent pour le store :

- `requeter` ne rend que les items d'**une seule partition**, triés par clé de
  tri, avec filtre par préfixe — comme un `query` avec `begins_with` ;
- `requeter_index` imite la projection **KEYS_ONLY** de l'index `par-type` :
  seules les clés (`PK`, `SK`, `GSI1PK`, `GSI1SK`) sont rendues, pas le document.
  Le store doit donc relire l'item — exactement ce qu'il fera sur AWS ;
- l'écriture est idempotente (mêmes clés ⇒ remplacement), comme `put_item`.

`TableEnPanne` sert à vérifier la traduction des erreurs : aucune exception boto3
ne doit remonter brute dans l'API (`ServiceIndisponibleError`, 502).
"""

from __future__ import annotations

from typing import Any

from jauge.common.errors import ServiceIndisponibleError


class FakeTableEtat:
    """Table d'état en mémoire, fidèle aux clés et à la projection de l'index."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self.journal: list[str] = []

    # --- port -----------------------------------------------------------------
    def ecrire(self, item: dict[str, Any]) -> None:
        """Écrit ou remplace un item (idempotent, comme `put_item`)."""
        self.journal.append(f"ecrire {item['PK']} {item['SK']}")
        self._items[(item["PK"], item["SK"])] = dict(item)

    def lire(self, partition: str, cle: str) -> dict[str, Any] | None:
        """Lit un item par ses deux clés, ou `None`."""
        self.journal.append(f"lire {partition} {cle}")
        item = self._items.get((partition, cle))
        return dict(item) if item else None

    def requeter(self, partition: str, prefixe_cle: str | None = None) -> list[dict[str, Any]]:
        """Items d'une seule partition, triés par clé de tri, filtrés par préfixe."""
        self.journal.append(f"requeter {partition} {prefixe_cle or '*'}")
        trouvés = [
            dict(item)
            for (pk, sk), item in sorted(self._items.items())
            if pk == partition and (prefixe_cle is None or sk.startswith(prefixe_cle))
        ]
        return trouvés

    def requeter_index(self, cle_index: str) -> list[dict[str, Any]]:
        """Index `par-type` en projection KEYS_ONLY : les clés, pas le document."""
        self.journal.append(f"requeter_index {cle_index}")
        return [
            {
                "PK": pk,
                "SK": sk,
                "GSI1PK": item.get("GSI1PK", ""),
                "GSI1SK": item.get("GSI1SK", ""),
            }
            for (pk, sk), item in sorted(self._items.items())
            if item.get("GSI1PK") == cle_index
        ]

    def supprimer(self, partition: str, cle: str) -> None:
        """Supprime un item (idempotent)."""
        self.journal.append(f"supprimer {partition} {cle}")
        self._items.pop((partition, cle), None)

    # --- introspection de test ---------------------------------------------------
    @property
    def nb_items(self) -> int:
        """Nombre d'items stockés (vérifie l'idempotence des écritures)."""
        return len(self._items)

    def partitions(self) -> set[str]:
        """Partitions présentes : prouve le cloisonnement structurel."""
        return {pk for pk, _ in self._items}


class TableEnPanne:
    """Table qui échoue toujours : vérifie la traduction des erreurs DynamoDB."""

    def __init__(self, erreur: Exception | None = None) -> None:
        self._erreur = erreur or RuntimeError("panne simulée du service")

    def ecrire(self, item: dict[str, Any]) -> None:
        """Échoue toujours."""
        raise self._erreur

    def lire(self, partition: str, cle: str) -> dict[str, Any] | None:
        """Échoue toujours."""
        raise self._erreur

    def requeter(self, partition: str, prefixe_cle: str | None = None) -> list[dict[str, Any]]:
        """Échoue toujours."""
        raise self._erreur

    def requeter_index(self, cle_index: str) -> list[dict[str, Any]]:
        """Échoue toujours."""
        raise self._erreur

    def supprimer(self, partition: str, cle: str) -> None:
        """Échoue toujours."""
        raise self._erreur


def panne_traduite() -> ServiceIndisponibleError:
    """Fabrique l'erreur attendue quand la table est en panne (aide aux tests)."""
    return ServiceIndisponibleError("DynamoDB indisponible ou accès refusé")
