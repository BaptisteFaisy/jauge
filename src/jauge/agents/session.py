"""Sessions persistantes par campagne (§12.4) et reprise d'une campagne.

Une campagne = une session : l'orchestrateur et ses spécialistes retrouvent
l'historique de la campagne (étapes franchies, campagne de rejeu lancée,
demande de validation en cours) même après un redémarrage du plan de contrôle.

Deux niveaux, complémentaires :

- `SessionCampagne` — état métier de la campagne, sérialisable, indépendant du
  SDK : c'est lui qui porte la reprise et l'audit (EF-ADM-02) ;
- `gestionnaire_session()` — `FileSessionManager` de Strands quand le SDK est
  installé, pour que les agents eux-mêmes reprennent leur conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from jauge.agents.sdk import creer_gestionnaire_session
from jauge.common.ids import horodatage_utc, jour_compact


@dataclass(frozen=True)
class EtapeCampagne:
    """Une étape franchie dans une campagne : qui, quoi, quand, avec quel résultat."""

    agent: str
    operation: str
    resume: str
    horodatage: str
    resultat: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionCampagne:
    """État persistant d'une campagne, un agent à la fois (§12.4).

    `campagne_id` sert d'identifiant de session Strands : une campagne reprise
    repart du même historique.
    """

    campagne_id: str
    tenant_id: str
    application_id: str
    etapes: list[EtapeCampagne] = field(default_factory=list)
    campagne_rejeu_id: str | None = None
    validation_id: str | None = None
    recommandation_id: str | None = None
    terminee: bool = False

    def enregistrer_etape(
        self,
        agent: str,
        operation: str,
        resume: str,
        resultat: dict[str, Any] | None = None,
    ) -> EtapeCampagne:
        """Ajoute une étape au fil de la campagne."""
        etape = EtapeCampagne(
            agent=agent,
            operation=operation,
            resume=resume,
            horodatage=horodatage_utc(datetime.now(UTC)),
            resultat=dict(resultat or {}),
        )
        self.etapes.append(etape)
        return etape

    def reprendre(self, campagne_rejeu_id: str) -> None:
        """Mémorise la campagne de rejeu lancée par le planificateur."""
        self.campagne_rejeu_id = campagne_rejeu_id
        self.enregistrer_etape(
            agent="planificateur_rejeu",
            operation="start_replay",
            resume=f"Rejeu lancé : {campagne_rejeu_id}",
            resultat={"campagne_id": campagne_rejeu_id},
        )

    def attendre_validation(self, demande_id: str) -> None:
        """Note l'interruption de validation humaine (§12.3)."""
        self.validation_id = demande_id
        self.enregistrer_etape(
            agent="redacteur",
            operation="validation_humaine",
            resume="Recommandation en attente d'approbation humaine",
            resultat={"demande_id": demande_id},
        )

    def cloturer(self, recommandation_id: str) -> None:
        """Clôt la campagne après enregistrement de la recommandation."""
        self.recommandation_id = recommandation_id
        self.terminee = True
        self.enregistrer_etape(
            agent="redacteur",
            operation="save_recommendation",
            resume=f"Recommandation enregistrée : {recommandation_id}",
            resultat={"recommandation_id": recommandation_id},
        )

    def resume(self) -> dict[str, Any]:
        """Résumé de la campagne, pour l'agent chef d'orchestre et le portail."""
        return {
            "campagne_id": self.campagne_id,
            "tenant_id": self.tenant_id,
            "application_id": self.application_id,
            "campagne_rejeu_id": self.campagne_rejeu_id,
            "validation_id": self.validation_id,
            "recommandation_id": self.recommandation_id,
            "terminee": self.terminee,
            "etapes": [
                {"agent": etape.agent, "operation": etape.operation, "resume": etape.resume}
                for etape in self.etapes
            ],
        }

    def gestionnaire_session(self, repertoire: str | None = None) -> Any:
        """Gestionnaire de session Strands de la campagne, ou `None` sans SDK."""
        return creer_gestionnaire_session(self.campagne_id, repertoire)

    @classmethod
    def pour_la_campagne(
        cls, campagne_id: str, tenant_id: str, application_id: str
    ) -> SessionCampagne:
        """Ouvre (ou rouvre) la session d'une campagne."""
        return cls(campagne_id=campagne_id, tenant_id=tenant_id, application_id=application_id)

    def cle_de_jour(self) -> str:
        """Clé de partition du jour, utile au journal d'audit (EF-ADM-02)."""
        return jour_compact(datetime.now(UTC))
