"""Ticket de changement de modèle pour une application hors passerelle (EF-BAS-04).

Une application qui appelle Bedrock directement reste couverte par l'attribution,
la détection et le rejeu ; seule la bascule automatique lui échappe (§11.5). Pour
elle, Jauge **décrit** le changement d'identifiant de modèle à faire, dans un
ticket, et ne l'exécute pas : la modification appartient à l'équipe qui exploite
l'application.

- EF-BAS-04 : ouvrir un ticket décrivant le changement.
- EF-REC-02 / ENF-IA-02 : le ticket n'est produit que sur une recommandation
  approuvée ; rien n'est appliqué par Jauge.
- ENF-CONF-01 : le ticket ne cite ni requête ni réponse, seulement des
  identifiants, des volumes et des montants.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from jauge.common.errors import (
    ApprobationRequiseError,
    CloisonnementError,
    ValidationError,
)
from jauge.common.ids import aleatoire, horodatage_utc
from jauge.models.recommendation import Recommandation, StatutRecommandation
from jauge.models.tenant import Application


@dataclass(frozen=True)
class TicketChangementModele:
    """Ticket décrivant un changement d'identifiant de modèle à faire côté app."""

    id: str
    tenant_id: str
    application_id: str
    recommandation_id: str
    modele_actuel: str
    modele_cible: str
    titre: str
    corps: str
    economie_attendue_usd: float
    cree_le: str


class EmetteurTicket(Protocol):
    """Système de tickets du client ou du MSP (Jira, ServiceNow, e-mail…)."""

    def ouvrir(self, ticket: TicketChangementModele) -> str:
        """Enregistre le ticket et renvoie sa référence externe."""
        ...


class FakeEmetteurTicket:
    """Émetteur fictif : garde les tickets en mémoire (tests, démo)."""

    def __init__(self) -> None:
        self.tickets: list[TicketChangementModele] = []

    def ouvrir(self, ticket: TicketChangementModele) -> str:
        self.tickets.append(ticket)
        return f"DEMO-{len(self.tickets)}"


def _entete_du_ticket(application: Application, recommandation: Recommandation) -> list[str]:
    """En-tête : quoi changer et pour quelle application."""
    return [
        f"# Changement de modèle — {application.nom}",
        "",
        "Application hors passerelle : la bascule est à faire par l'équipe qui",
        "exploite l'application (Jauge ne modifie pas son code).",
        "",
        "## Changement demandé",
        "",
        f"- Identifiant de modèle actuel : `{application.modele_actuel}`",
        f"- Identifiant de modèle cible : `{recommandation.targetModel}`",
        f"- Environnement : {application.environnement}",
        f"- Équipe : {application.equipe}",
        "",
    ]


def _justification_du_ticket(
    application: Application, recommandation: Recommandation, volume_mensuel: int
) -> list[str]:
    """Chiffres déjà calculés par le code, recopiés tels quels (ENF-IA-01)."""
    return [
        "## Justification mesurée",
        "",
        f"- Taux d'équivalence observé au rejeu : {recommandation.quality.equivalenceRate:.3f}",
        "- Borne basse de l'intervalle de confiance : "
        f"{recommandation.quality.confidenceIntervalLow:.3f}",
        f"- Seuil de qualité de l'application : {application.seuil_qualite:.3f}",
        f"- Coût par requête avant : {recommandation.cost.costPerRequestBefore:.6f} $",
        f"- Coût par requête après : {recommandation.cost.costPerRequestAfter:.6f} $",
        f"- Économie mensuelle attendue : {recommandation.cost.expectedMonthlySaving:.2f} $",
        f"- Volume mensuel de référence : {volume_mensuel}",
        f"- Campagne de rejeu : {recommandation.campaignId} ({recommandation.sampleSize} requêtes)",
        "",
    ]


def corps_du_ticket(
    application: Application,
    recommandation: Recommandation,
    volume_mensuel: int | None = None,
) -> str:
    """Corps Markdown du ticket : quoi changer, pourquoi, comment vérifier.

    Le texte est rédigé à partir de valeurs déjà calculées (ENF-IA-01) : aucun
    modèle n'intervient, et aucun contenu de requête n'y figure (ENF-CONF-01).
    """
    lignes = _entete_du_ticket(application, recommandation)
    lignes += _justification_du_ticket(
        application, recommandation, volume_mensuel or recommandation.cost.monthlyRequests
    )
    lignes += [
        "## Étapes",
        "",
        "1. Remplacer l'identifiant de modèle dans la configuration de l'application.",
        "2. Déployer sur un environnement de recette et rejouer la suite de tests.",
        "3. Déployer en production par paliers si l'application le permet.",
        "4. Surveiller le taux d'erreur et la qualité perçue pendant 30 jours.",
        "",
        "## Vérification",
        "",
        "Trente jours après, Jauge compare le coût par requête avant et après, à",
        "volume constant, et confirme l'économie sur les dollars facturés.",
        "",
        f"Recommandation d'origine : {recommandation.id}",
    ]
    return "\n".join(lignes)


def generer_ticket(
    tenant_id: str,
    application: Application,
    recommandation: Recommandation,
    moment: datetime | None = None,
    volume_mensuel: int | None = None,
) -> TicketChangementModele:
    """Construit le ticket d'une application hors passerelle, sans rien exécuter.

    Refuse une application qui passe par la passerelle : pour elle, la bascule se
    fait par règle (`controller.ControlleurBascule`), pas par ticket.
    """
    if application.tenant_id != tenant_id or recommandation.tenantId != tenant_id:
        raise CloisonnementError(
            "Application ou recommandation hors du client demandé.",
            tenant_id=tenant_id,
            application_id=application.id,
        )
    if application.passe_par_passerelle:
        raise ValidationError(
            "Application couverte par la passerelle : utiliser la bascule par règle.",
            application_id=application.id,
        )
    if recommandation.status is not StatutRecommandation.APPROUVEE:
        raise ApprobationRequiseError(
            "Un ticket de changement exige une recommandation approuvée (ENF-IA-02).",
            recommandation_id=recommandation.id,
            statut=recommandation.status.value,
        )
    instant = moment or datetime.now(UTC)
    return TicketChangementModele(
        id=aleatoire("rollout"),
        tenant_id=tenant_id,
        application_id=application.id,
        recommandation_id=recommandation.id,
        modele_actuel=application.modele_actuel,
        modele_cible=recommandation.targetModel,
        titre=(
            f"[Jauge] {application.nom} : passer de {application.modele_actuel} "
            f"à {recommandation.targetModel}"
        ),
        corps=corps_du_ticket(application, recommandation, volume_mensuel),
        economie_attendue_usd=recommandation.cost.expectedMonthlySaving,
        cree_le=horodatage_utc(instant),
    )


def ouvrir_ticket(
    emetteur: EmetteurTicket,
    tenant_id: str,
    application: Application,
    recommandation: Recommandation,
    moment: datetime | None = None,
    volume_mensuel: int | None = None,
) -> tuple[TicketChangementModele, str]:
    """Génère le ticket puis le confie au système de tickets du client."""
    ticket = generer_ticket(tenant_id, application, recommandation, moment, volume_mensuel)
    reference = emetteur.ouvrir(ticket)
    return ticket, reference
