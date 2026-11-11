"""Clés et sérialisation de la table d'état DynamoDB du plan de contrôle.

Schéma — celui que crée `infra/control-plane/jauge_control_plane/constructs/
storage.py` : une table unique `PK` / `SK`, plus l'index global `par-type`
(`GSI1PK` / `GSI1SK`, projection KEYS_ONLY).

Deux familles de partitions :

- `TENANT#<tenant_id>` — tout ce qui appartient à un client (applications,
  agrégats, rapprochements, leviers, campagnes, recommandations, bascules).
  Le cloisonnement (ENF-SEC-02) y est **structurel** : chaque requête ne porte
  qu'une partition, aucun chemin de code ne peut en lire une autre.
- `REGISTRY#<registre>` — les entités dont le modèle ne porte pas de
  `tenant_id` : la liste des clients et les vérifications d'économie. L'index
  `par-type` ne peut pas les servir à leur place : sa projection KEYS_ONLY ne
  renvoie pas le document.

Le document pydantic est sérialisé en JSON dans l'attribut `doc` : `Decimal`,
dates et champs absents passent sans conversion artisanale, et ajouter un champ
à un modèle n'impose aucune migration. Quelques attributs sont dupliqués à plat
(`type`, `tenant_id`, `app_id`, `jour`) pour filtrer sans désérialiser et pour
alimenter l'index `par-type`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

#: Types d'entités — préfixe de `SK` et valeur de l'index `par-type`.
TENANT = "TENANT"
APPLICATION = "APP"
AGREGAT = "AGG"
RAPPROCHEMENT = "RECON"
LEVIER = "LEVER"
CAMPAGNE = "CAMP"
RECOMMANDATION = "REC"
BASCULE = "SWITCH"
VERIFICATION = "VERIF"

#: Registres transversaux (entités sans `tenant_id` dans leur modèle).
PARTITION_REGISTRE = "REGISTRY"
REGISTRE_TENANTS = "TENANTS"
REGISTRE_VERIFICATIONS = "VERIFICATIONS"

#: Attribut portant le document pydantic sérialisé.
ATTRIBUT_DOCUMENT = "doc"


def partition_client(tenant_id: str) -> str:
    """Clé de partition d'un client : toute lecture part de là (ENF-SEC-02)."""
    return f"TENANT#{tenant_id}"


def partition_registre(nom: str) -> str:
    """Clé de partition d'un registre transversal (clients, vérifications)."""
    return f"{PARTITION_REGISTRE}#{nom}"


def cle_tri(type_entite: str, *parties: object) -> str:
    """Clé de tri `<TYPE>#<partie>#…` ; sans partie, le préfixe `<TYPE>#`.

    Le type en préfixe permet un `begins_with` : lister les campagnes d'un
    client est une requête sur préfixe, jamais un scan de table. Le « # » final
    est indispensable : sans lui, le préfixe « REC » (recommandations)
    matcherait « RECON#… » (rapprochements) et la lecture renverrait des
    entités du mauvais type.
    """
    jointes = "#".join(str(partie) for partie in parties if str(partie) != "")
    return f"{type_entite}#{jointes}"


def cle_index(type_entite: str, tenant_id: str, identifiant: str) -> tuple[str, str]:
    """(`GSI1PK`, `GSI1SK`) de l'index `par-type`.

    `GSI1SK` commence par le client : la vue de parc du MSP (« toutes les
    applications d'un client », « toutes les recommandations en attente ») est
    un `begins_with` sur l'index, pas un scan.
    """
    return f"TYPE#{type_entite}", f"{tenant_id}#{identifiant}"


def vers_item(
    partition: str,
    cle: str,
    type_entite: str,
    modele: BaseModel,
    *,
    tenant_id: str = "",
    attributs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construit l'item DynamoDB d'une entité (document JSON + attributs plats).

    Les attributs vides sont omis : DynamoDB n'accepte pas de valeur nulle dans
    un item, et une clé d'index vide ferait échouer l'écriture.
    """
    index_pk, index_sk = cle_index(type_entite, tenant_id or partition, cle)
    item: dict[str, Any] = {
        "PK": partition,
        "SK": cle,
        "type": type_entite,
        "tenant_id": tenant_id,
        "GSI1PK": index_pk,
        "GSI1SK": index_sk,
        ATTRIBUT_DOCUMENT: modele.model_dump_json(),
    }
    item.update(attributs or {})
    return {k: v for k, v in item.items() if v not in (None, "")}


def depuis_item[Modele: BaseModel](
    item: dict[str, Any] | None, modele: type[Modele]
) -> Modele | None:
    """Reconstruit l'entité depuis un item, ou `None` si l'item est absent."""
    if not item:
        return None
    brut = item.get(ATTRIBUT_DOCUMENT)
    if not isinstance(brut, str) or not brut:
        return None
    return modele.model_validate_json(brut)


def jour_iso(jour: date) -> str:
    """Jour au format ISO : le tri lexicographique de `SK` est le tri chronologique."""
    return jour.isoformat()
