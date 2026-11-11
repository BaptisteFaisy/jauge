"""Plan de données : le côté client (textes, requêtes rejouables, profil agrégé).

ENF-CONF-01 : ce module est le **plan de données**. Il porte les textes des
requêtes et des réponses ; aucun de ces textes ne remonte vers le plan de
contrôle ni vers un agent. Seuls le profil agrégé, les identifiants et les
mesures sortent.

Le plan de données se remplit depuis les **journaux d'invocation bruts** (les
lignes JSON de Bedrock) via `jauge.integration.lecture_journaux`, qui partage la
même source que le module d'attribution (M1) : les agrégats du plan de contrôle
et le profil rejouable ne peuvent pas diverger.

Exigences visées : EF-REJ-01 (échantillon reproductible), EF-ATT-02 (journaux
d'invocation), EF-LEV-01..06 (profil agrégé des requêtes), ENF-CONF-01.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jauge.levers.base import ProfilRequetes
from jauge.models.campaign import ParametresRejeu


@dataclass(frozen=True)
class RequeteRejouable:
    """Métadonnées d'une requête rejouable (aucun texte)."""

    requete_id: str
    application_id: str
    type_requete: str
    longueur_caracteres: int
    jetons_entree_origine: int
    parametres: ParametresRejeu = field(default_factory=ParametresRejeu)


@dataclass
class CampagneDataPlane:
    """Trace d'une campagne de rejeu : mesures, verdicts et paires (data plane)."""

    campagne_id: str
    resultats: list[object] = field(default_factory=list)
    clusters: list[object] = field(default_factory=list)
    paires: list[object] = field(default_factory=list)


class PlanDeDonnees:
    """Le plan de données du client : textes, requêtes, profil et campagnes.

    Cloisonnement : chaque entrée est indexée par le couple
    `(tenant_id, application_id)`. Le plan ne rend jamais une donnée d'un autre
    client, et les textes ne quittent jamais ce module (ENF-CONF-01).
    """

    def __init__(self) -> None:
        self._profils: dict[tuple[str, str], ProfilRequetes] = {}
        self._requetes: dict[tuple[str, str], list[RequeteRejouable]] = {}
        self._textes: dict[tuple[str, str], dict[str, str]] = {}
        self._types: dict[str, str] = {}
        self._campagnes: dict[str, CampagneDataPlane] = {}

    def enregistrer_profil(
        self, tenant_id: str, application_id: str, profil: ProfilRequetes
    ) -> None:
        """Enregistre le profil agrégé d'une application."""
        self._profils[(tenant_id, application_id)] = profil

    def profil(self, tenant_id: str, application_id: str) -> ProfilRequetes | None:
        """Profil agrégé d'une application, s'il a été observé."""
        return self._profils.get((tenant_id, application_id))

    def enregistrer_requetes(
        self,
        tenant_id: str,
        application_id: str,
        requetes: list[RequeteRejouable],
        textes: dict[str, str],
    ) -> None:
        """Enregistre les requêtes rejouables et leurs textes (data plane)."""
        cle = (tenant_id, application_id)
        self._requetes[cle] = list(requetes)
        self._textes[cle] = dict(textes)
        for requete in requetes:
            self._types[requete.requete_id] = requete.type_requete

    def type_de(self, requete_id: str) -> str:
        """Type de requête observé pour un identifiant (« general » par défaut)."""
        return self._types.get(requete_id, "general")

    def requetes(self, tenant_id: str, application_id: str) -> list[RequeteRejouable]:
        """Requêtes rejouables connues d'une application."""
        return list(self._requetes.get((tenant_id, application_id), []))

    def textes(self, tenant_id: str, application_id: str) -> dict[str, str]:
        """Textes des requêtes d'une application (ne quittent jamais le plan)."""
        return dict(self._textes.get((tenant_id, application_id), {}))

    def enregistrer_campagne(self, campagne: CampagneDataPlane) -> None:
        """Enregistre la trace d'une campagne de rejeu."""
        self._campagnes[campagne.campagne_id] = campagne

    def campagne(self, campagne_id: str) -> CampagneDataPlane | None:
        """Trace d'une campagne de rejeu."""
        return self._campagnes.get(campagne_id)
