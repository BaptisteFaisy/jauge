"""Ports locaux de l'API pour les actions pas encore livrées par le socle.

L'API du plan de contrôle ne dépend d'aucun module métier en cours d'écriture :
elle parle à des ports (protocoles). Tant que M4 (rejeu), M9 (rapports) et M7
(agents Strands) ne sont pas livrés, les implémentations par défaut répondent
`upstream_unavailable` (502) avec un message explicite. Le mode démo fournit
des implémentations locales, déterministes et sans réseau.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Protocol

from pydantic import BaseModel, Field

from jauge.common.errors import PlafondDepasseError, ServiceIndisponibleError, ValidationError
from jauge.common.ids import aleatoire
from jauge.models.campaign import Campagne, StatutCampagne
from jauge.models.tenant import Application, Tenant
from jauge.store.protocol import Store

MESSAGE_M4 = "Le lancement de campagne sera fourni par le module M4 (rejeu)."
MESSAGE_M9 = "La génération de rapports sera fournie par le module M9 (rapports)."
MESSAGE_M7 = "Les agents Strands (module M7) ne sont pas encore branchés sur ce plan de contrôle."


class DemandeCampagne(BaseModel):
    """Corps de `POST /v1/applications/{id}/campaigns`."""

    modeles_candidats: list[str] = Field(default_factory=list)
    taille_echantillon: int | None = Field(default=None, ge=10, le=2000)
    graine_aleatoire: int = 42
    modele_juge_id: str | None = None


class CampagnePort(Protocol):
    """Lancement d'une campagne de rejeu (M4)."""

    def lancer(
        self, tenant: Tenant, application: Application, demande: DemandeCampagne
    ) -> Campagne:
        """Crée et planifie une campagne de rejeu."""
        ...


class RapportPort(Protocol):
    """Rapports mensuels (M9)."""

    def generer(self, tenant_id: str, debut: date, fin: date) -> dict[str, object]:
        """Génère un rapport pour la période demandée."""
        ...

    def obtenir(self, rapport_id: str) -> dict[str, object] | None:
        """Rend un rapport déjà généré."""
        ...


class ChatPort(Protocol):
    """Questions en langage naturel (M7)."""

    def repondre(self, tenant_id: str, question: str) -> dict[str, object]:
        """Répond à une question sur les coûts du client."""
        ...


class PortCampagnesIndisponible:
    """Implémentation par défaut : M4 n'est pas encore livré."""

    def lancer(
        self, tenant: Tenant, application: Application, demande: DemandeCampagne
    ) -> Campagne:
        """Refuse clairement le lancement tant que M4 n'existe pas."""
        raise ServiceIndisponibleError(MESSAGE_M4, module="M4", application_id=application.id)


class PortRapportsIndisponible:
    """Implémentation par défaut : M9 n'est pas encore livré."""

    def generer(self, tenant_id: str, debut: date, fin: date) -> dict[str, object]:
        """Refuse clairement la génération tant que M9 n'existe pas."""
        raise ServiceIndisponibleError(MESSAGE_M9, module="M9", tenant_id=tenant_id)

    def obtenir(self, rapport_id: str) -> dict[str, object] | None:
        """Refuse clairement la lecture tant que M9 n'existe pas."""
        raise ServiceIndisponibleError(MESSAGE_M9, module="M9", rapport_id=rapport_id)


class PortChatIndisponible:
    """Implémentation par défaut : M7 n'est pas encore livré."""

    def repondre(self, tenant_id: str, question: str) -> dict[str, object]:
        """Refuse clairement la question tant que M7 n'existe pas."""
        raise ServiceIndisponibleError(MESSAGE_M7, module="M7", tenant_id=tenant_id)


def cout_moyen_par_requete(store: Store, tenant_id: str) -> float:
    """Coût moyen par requête observé sur les agrégats du client (code, pas LLM)."""
    agregats = store.lister_agregats(tenant_id)
    total_cout = sum(a.cout_recalcule_usd for a in agregats)
    total_requetes = sum(a.nombre_requetes for a in agregats)
    if total_requetes <= 0:
        return 0.0
    return total_cout / total_requetes


class PortCampagnesDemo:
    """Implémentation de démonstration : campagne créée en mémoire, coût estimé par le code."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def lancer(
        self, tenant: Tenant, application: Application, demande: DemandeCampagne
    ) -> Campagne:
        """Crée une campagne planifiée après contrôle du plafond du client."""
        candidats = demande.modeles_candidats or [
            m for m in tenant.modeles_autorises if m != application.modele_actuel
        ]
        if not candidats:
            raise ValidationError(
                "Aucun modèle candidat fourni ni autorisé pour ce client",
                tenant_id=tenant.id,
            )
        interdits = [
            m for m in candidats if tenant.modeles_autorises and m not in tenant.modeles_autorises
        ]
        if interdits:
            raise ValidationError("Modèle candidat non autorisé pour ce client", modeles=interdits)
        taille = demande.taille_echantillon or 200
        estimation = round(
            taille * (len(candidats) + 1) * cout_moyen_par_requete(self._store, tenant.id), 4
        )
        if estimation > tenant.plafond_campagne_usd:
            raise PlafondDepasseError(
                "Coût estimé de la campagne au-dessus du plafond du client",
                cout_estime_usd=estimation,
                plafond_usd=tenant.plafond_campagne_usd,
            )
        campagne = Campagne(
            id=aleatoire("campaign"),
            tenant_id=tenant.id,
            application_id=application.id,
            modeles_candidats=candidats,
            taille_echantillon=taille,
            graine_aleatoire=demande.graine_aleatoire,
            modele_juge_id=demande.modele_juge_id or "modele-juge-demo",
            statut=StatutCampagne.PLANIFIEE,
            cout_estime_usd=estimation,
        )
        self._store.enregistrer_campagne(campagne)
        return campagne


class PortRapportsDemo:
    """Implémentation de démonstration : rapport JSON à partir des agrégats du store.

    Les chiffres sont calculés par le socle (`SyntheseCout`) ; seule la phrase
    de synthèse est statique. Aucun LLM, aucun appel réseau (ENF-IA-01).
    """

    def __init__(self, store: Store) -> None:
        self._store = store
        self._rapports: dict[str, dict[str, object]] = {}

    def generer(self, tenant_id: str, debut: date, fin: date) -> dict[str, object]:
        """Produit un rapport minimal et le conserve en mémoire."""
        synthese = self._store.synthese_cout(tenant_id, debut, fin)
        rapport: dict[str, object] = {
            "id": aleatoire("report"),
            "tenant_id": tenant_id,
            "periode_debut": debut.isoformat(),
            "periode_fin": fin.isoformat(),
            "synthese": synthese.model_dump(mode="json"),
            "synthese_redigee": (
                f"Dépense recalculée de {synthese.cout_total_usd} USD sur la période, "
                f"couverture d'attribution de {synthese.couverture_attribution_pourcentage} %."
            ),
        }
        self._rapports[str(rapport["id"])] = rapport
        return rapport

    def obtenir(self, rapport_id: str) -> dict[str, object] | None:
        """Rend un rapport déjà généré."""
        return self._rapports.get(rapport_id)


class PortChatDemo:
    """Réponse déterministe aux questions de coût, sans agent ni réseau.

    Le texte s'appuie uniquement sur des chiffres déjà calculés (ENF-IA-01) et
    ne renvoie jamais de contenu de requête (ENF-CONF-01).
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    def repondre(self, tenant_id: str, question: str) -> dict[str, object]:
        """Répond avec la dépense récente du client et le nombre de leviers ouverts."""
        fin = date.today()
        debut = fin - timedelta(days=30)
        synthese = self._store.synthese_cout(tenant_id, debut, fin)
        leviers = self._store.lister_leviers(tenant_id)
        chiffres = {
            "cout_total_usd": synthese.cout_total_usd,
            "periode_debut": debut.isoformat(),
            "periode_fin": fin.isoformat(),
            "leviers_detectes": len(leviers),
        }
        texte = (
            f"Sur les 30 derniers jours, la dépense recalculée atteint "
            f"{synthese.cout_total_usd} USD et {len(leviers)} leviers sont détectés. "
            f"Question reçue : « {question[:200]} »."
        )
        return {"reponse": texte, "chiffres": chiffres, "mode": "demonstration"}
