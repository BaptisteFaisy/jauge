"""Benchmark inter-clients anonymisé (`GET /v1/benchmarks/parc`).

Le MSP compare les coûts unitaires de ses clients (EF-RAP-02). Cette
publication **traverse la frontière des clients** : elle applique donc la
politique de confidentialité de chaque client avant de sortir un chiffre :

- **k-anonymité** : une cellule fondée sur moins de `k` entités n'est jamais
  publiée — elle est supprimée, ou regroupée dans « autres » si le groupe
  lui-même atteint `k` ;
- **bruit différentiellement privé** : chaque valeur publiée porte son
  `epsilon` et sa `sensibilité` — un chiffre sans son epsilon n'a pas de sens.

La politique retenue est la **plus stricte** parmi celles des clients
concernés (k le plus élevé, epsilon le plus faible) : le seuil le plus
protecteur l'emporte, jamais le plus permissif.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from jauge.api.audit import journaliser
from jauge.api.auth import RoleUtilisateur
from jauge.api.deps import DepAudit, DepStore, DepUtilisateur
from jauge.api.routes.killswitch import DepSeuilsAnonymite
from jauge.api.tenancy import AccesRefuseError
from jauge.reports.anonymisation import (
    PolitiqueAnonymite,
    SeuilsAnonymiteStore,
    publier_benchmark_parc,
)

router = APIRouter(tags=["benchmarks"])

#: Rôles MSP autorisés à voir une comparaison inter-clients.
ROLES_BENCHMARK = (
    RoleUtilisateur.ADMINISTRATEUR_PLATEFORME,
    RoleUtilisateur.ANALYSTE_FINOPS,
    RoleUtilisateur.RESPONSABLE_COMPTE,
)


class ReponseBenchmark(BaseModel):
    """Publication inter-clients : valeurs bruitées, k, epsilon et suppressions."""

    k_applique: int
    epsilon: float
    sensibilite: float
    nombre_supprimees: int
    nombre_regroupees: int
    valeurs: list[dict[str, Any]]


def _politique_la_plus_stricte(
    seuils: SeuilsAnonymiteStore, tenant_ids: list[str]
) -> PolitiqueAnonymite:
    """Politique retenue : k le plus élevé, epsilon le plus faible.

    `PolitiqueAnonymite` interdit `epsilon <= 0` : le minimum des epsilons est
    donc toujours une valeur strictement positive, et le k le plus élevé l'emporte.
    Sans client, on retombe sur les valeurs par défaut du module.
    """
    politiques = [seuils.obtenir_politique(tenant_id) for tenant_id in tenant_ids]
    if not politiques:
        return PolitiqueAnonymite()
    return PolitiqueAnonymite(
        k=max(politique.k for politique in politiques),
        epsilon=min(politique.epsilon for politique in politiques),
    )


@router.get("/v1/benchmarks/parc", response_model=ReponseBenchmark)
def benchmark_parc(
    utilisateur: DepUtilisateur,
    store: DepStore,
    seuils: DepSeuilsAnonymite,
    audit: DepAudit,
    dimension: Annotated[str, Query(min_length=1, max_length=64)] = "modele",
    debut: Annotated[date | None, Query()] = None,
    fin: Annotated[date | None, Query()] = None,
) -> ReponseBenchmark:
    """Compare les coûts unitaires des clients, après anonymisation.

    Seuls les rôles MSP transverse voient cette publication ; un rôle client
    reçoit un refus explicite (il n'a rien à voir des autres clients).
    """
    if utilisateur.role not in ROLES_BENCHMARK:
        raise AccesRefuseError(
            "Cross-customer benchmarks are restricted to MSP roles",
            role=utilisateur.role.value,
        )
    tenant_ids = [tenant.id for tenant in store.lister_tenants()]
    politique = _politique_la_plus_stricte(seuils, tenant_ids)
    publication = publier_benchmark_parc(
        store,
        tenant_ids,
        politique,
        dimension=dimension,
        debut=debut,
        fin=fin,
    )
    journaliser(
        audit,
        utilisateur,
        "benchmark_parc_publie",
        "benchmark",
        dimension,
        {
            "k_applique": publication.k_applique,
            "epsilon": publication.epsilon,
            "nombre_supprimees": publication.nombre_supprimees,
        },
        tenant_id=utilisateur.tenant_id,
    )
    return ReponseBenchmark(
        k_applique=publication.k_applique,
        epsilon=publication.epsilon,
        sensibilite=publication.sensibilite,
        nombre_supprimees=publication.nombre_supprimees,
        nombre_regroupees=publication.nombre_regroupees,
        valeurs=[valeur.vers_dict() for valeur in publication.valeurs],
    )
