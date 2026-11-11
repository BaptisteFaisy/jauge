"""Journal d'audit du plan de contrôle Jauge (EF-ADM-02, ENF-SEC-02).

Consigne chaque campagne, décision, bascule et tentative de violation de
cloisonnement. Les événements sont immuables et horodatés en UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field

from jauge.common.ids import aleatoire, horodatage_utc


class EvenementAudit(BaseModel):
    """Événement consigné dans le journal d'audit (§16, EF-ADM-02)."""

    id: str = Field(default_factory=lambda: aleatoire("audit"))
    horodatage: str = Field(default_factory=lambda: horodatage_utc(datetime.now(UTC)))
    tenant_id: str
    utilisateur_id: str
    role: str
    action: str
    ressource_type: str
    ressource_id: str
    details: dict[str, object] = Field(default_factory=dict)
    succes: bool = True
    statut_http: int = 200


class AuditSink(Protocol):
    """Contrat de persistance pour les événements d'audit."""

    def enregistrer(self, evenement: EvenementAudit) -> None:
        """Enregistre un événement dans le journal d'audit."""
        ...

    def lister(
        self,
        tenant_id: str | None = None,
        action: str | None = None,
        ressource_type: str | None = None,
    ) -> list[EvenementAudit]:
        """Liste les événements filtrés."""
        ...


def journaliser(
    audit: AuditSink,
    utilisateur: object,
    action: str,
    ressource_type: str,
    ressource_id: str,
    details: dict[str, object] | None = None,
    tenant_id: str | None = None,
    succes: bool = True,
    statut_http: int = 200,
) -> EvenementAudit:
    """Construit et enregistre un événement d'audit (raccourci des routes).

    `utilisateur` est un `jauge.api.auth.Utilisateur` ; le typage reste souple
    pour éviter une dépendance circulaire entre `audit` et `auth`.
    """
    identifiant = getattr(utilisateur, "id", "inconnu")
    role = getattr(utilisateur, "role", "")
    evenement = EvenementAudit(
        tenant_id=tenant_id or getattr(utilisateur, "tenant_id", "") or "",
        utilisateur_id=str(identifiant),
        role=str(getattr(role, "value", role)),
        action=action,
        ressource_type=ressource_type,
        ressource_id=ressource_id,
        details=details or {},
        succes=succes,
        statut_http=statut_http,
    )
    audit.enregistrer(evenement)
    return evenement


class MemoryAuditStore:
    """Implémentation en mémoire du journal d'audit pour les tests et la démo."""

    def __init__(self) -> None:
        self._evenements: list[EvenementAudit] = []

    def enregistrer(self, evenement: EvenementAudit) -> None:
        """Ajoute un événement au journal immuable."""
        self._evenements.append(evenement)

    def lister(
        self,
        tenant_id: str | None = None,
        action: str | None = None,
        ressource_type: str | None = None,
    ) -> list[EvenementAudit]:
        """Filtre les événements par critères."""
        resultats = self._evenements
        if tenant_id:
            resultats = [e for e in resultats if e.tenant_id == tenant_id]
        if action:
            resultats = [e for e in resultats if e.action == action]
        if ressource_type:
            resultats = [e for e in resultats if e.ressource_type == ressource_type]
        return list(resultats)
