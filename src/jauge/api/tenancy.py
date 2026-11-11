"""Cloisonnement par client (ENF-SEC-02, scénario de recette R11).

Toute route qui expose des données passe par ici : résolution du tenant depuis
le contexte d'authentification, contrôle de l'appartenance de la ressource et
journalisation de l'audit. Un accès inter-clients lève `CloisonnementError`
(403) **et** laisse une trace dans le journal d'audit.
"""

from __future__ import annotations

from collections.abc import Callable

from jauge.api.audit import AuditSink, EvenementAudit
from jauge.api.auth import RoleUtilisateur, Utilisateur
from jauge.common.errors import CloisonnementError, IntrouvableError, JaugeError
from jauge.models.tenant import Tenant
from jauge.store.protocol import Store


class AccesRefuseError(JaugeError):
    """Rôle insuffisant pour l'action demandée (403)."""

    code = "forbidden"
    statut_http = 403


def exiger_role(utilisateur: Utilisateur, *roles: RoleUtilisateur) -> None:
    """Vérifie que l'utilisateur détient l'un des rôles attendus."""
    if utilisateur.role not in roles:
        raise AccesRefuseError(
            "Rôle insuffisant pour cette action",
            role=utilisateur.role.value,
            roles_attendus=[r.value for r in roles],
        )


def tenant_autorise(utilisateur: Utilisateur, tenant_id: str) -> bool:
    """Détermine si l'utilisateur peut atteindre les données du tenant visé."""
    if utilisateur.est_admin or utilisateur.tenant_id == tenant_id:
        return True
    return utilisateur.est_msp and tenant_id in utilisateur.tenants_autorises


def journaliser_refus(
    audit: AuditSink | None,
    utilisateur: Utilisateur,
    tenant_id: str,
    action: str,
    ressource_type: str,
    ressource_id: str,
) -> None:
    """Consigne une tentative d'accès refusée (événement de sécurité)."""
    if audit is None:
        return
    audit.enregistrer(
        EvenementAudit(
            tenant_id=tenant_id,
            utilisateur_id=utilisateur.id,
            role=utilisateur.role.value,
            action=action,
            ressource_type=ressource_type,
            ressource_id=ressource_id,
            details={"motif": "cloisonnement", "tenant_appelant": utilisateur.tenant_id},
            succes=False,
            statut_http=403,
        )
    )


def verifier_acces_tenant(
    utilisateur: Utilisateur,
    tenant_id: str,
    audit: AuditSink | None = None,
    action: str = "lecture",
    ressource_type: str = "tenant",
    ressource_id: str | None = None,
) -> str:
    """Contrôle l'accès au tenant et rend son identifiant, ou lève une erreur."""
    if tenant_autorise(utilisateur, tenant_id):
        return tenant_id
    journaliser_refus(
        audit, utilisateur, tenant_id, action, ressource_type, ressource_id or tenant_id
    )
    raise CloisonnementError(
        "Accès refusé aux données de ce client",
        tenant_demande=tenant_id,
        tenant_appelant=utilisateur.tenant_id,
    )


def obtenir_tenant_controle(
    store: Store, utilisateur: Utilisateur, tenant_id: str, audit: AuditSink | None = None
) -> Tenant:
    """Rend le tenant après contrôle d'existence puis de cloisonnement."""
    verifier_acces_tenant(utilisateur, tenant_id, audit, "lecture_tenant", "tenant")
    tenant = store.obtenir_tenant(tenant_id)
    if tenant is None:
        raise IntrouvableError("Client introuvable", tenant_id=tenant_id)
    return tenant


def _tenants_a_parcourir(store: Store, utilisateur: Utilisateur) -> list[str]:
    """Liste les tenants à interroger pour localiser une ressource par identifiant.

    On parcourt toujours tout le parc : c'est le seul moyen de distinguer
    « ressource inexistante » (404) de « ressource d'un autre client » (403),
    distinction exigée par le scénario R11.
    """
    identifiants = [t.id for t in store.lister_tenants()]
    if utilisateur.tenant_id and utilisateur.tenant_id not in identifiants:
        identifiants.insert(0, utilisateur.tenant_id)
    return identifiants


def localiser[T](
    store: Store,
    utilisateur: Utilisateur,
    ressource_id: str,
    ressource_type: str,
    obtenir: Callable[[str, str], T | None],
    audit: AuditSink | None = None,
    action: str = "lecture",
) -> T:
    """Localise une ressource par identifiant puis vérifie le cloisonnement."""
    for tenant_id in _tenants_a_parcourir(store, utilisateur):
        ressource = obtenir(tenant_id, ressource_id)
        if ressource is not None:
            verifier_acces_tenant(
                utilisateur, tenant_id, audit, action, ressource_type, ressource_id
            )
            return ressource
    raise IntrouvableError(f"Ressource introuvable : {ressource_type}", ressource_id=ressource_id)
