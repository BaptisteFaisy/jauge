"""Briques partagées par les adaptateurs du plan de contrôle (M17).

Le câblage **branche** les ports des agents (`jauge.agents.ports`) sur les
modules réels : il ne réécrit aucun calcul (ENF-IA-01). Ce module regroupe ce
qui sert à plusieurs adaptateurs :

- la lecture cloisonnée du référentiel : un client, une application (ENF-SEC-02) ;
- la correspondance entre le type de requête observé et le **type de tâche** du
  module d'évaluation (EF-EVA-02 : les contrôles déterministes dépendent du
  type de tâche) ;
- la famille et la fenêtre de contexte d'un modèle, déduites de son
  identifiant : le dépôt ne porte pas encore de catalogue de référence des
  modèles du marché (besoin de socle signalé dans la PR d'intégration).

Exigences visées : ENF-SEC-02, ENF-IA-01, EF-ADM-01, §12.1.
"""

from __future__ import annotations

from jauge.common.errors import CloisonnementError, IntrouvableError
from jauge.models.tenant import Application, Tenant
from jauge.store.protocol import Store

#: Types de requêtes observés → type de tâche du module d'évaluation.
TYPES_TACHE: dict[str, str] = {
    "classification": "classification",
    "classement": "classification",
    "extraction": "extraction",
    "redaction": "redaction",
    "rédaction": "redaction",
    "resume": "redaction",
    "résumé": "redaction",
    "generation": "general",
    "génération": "general",
    "question": "general",
    "recherche": "general",
    "general": "general",
}

#: Mots-clés du type de tâche, cherchés dans la description d'une application.
MOTS_CLES_TACHE: tuple[tuple[str, str], ...] = (
    ("classement", "classification"),
    ("classification", "classification"),
    ("extraction", "extraction"),
    ("résumé", "redaction"),
    ("resume", "redaction"),
    ("rédaction", "redaction"),
    ("redaction", "redaction"),
)

#: Familles de modèles déduites de l'identifiant (hypothèses de démonstration).
FAMILLES_MODELES: tuple[tuple[str, str], ...] = (
    ("opus", "grand"),
    ("sonnet", "moyen"),
    ("haiku", "petit"),
)

#: Fenêtre de contexte par défaut des modèles connus (hypothèse de démonstration).
FENETRE_CONTEXTE_DEFAUT = 200_000


def charger_tenant(store: Store, tenant_id: str) -> Tenant:
    """Rend le client demandé, ou refuse l'accès (ENF-SEC-02)."""
    tenant = store.obtenir_tenant(tenant_id)
    if tenant is None:
        raise CloisonnementError(
            f"Client inconnu : {tenant_id}", tenant_id=tenant_id, operation="tenant"
        )
    return tenant


def charger_application(store: Store, tenant_id: str, application_id: str) -> Application:
    """Rend l'application demandée, en refusant l'accès inter-clients.

    Une application d'un autre client lève `CloisonnementError` (403) ; une
    application inexistante lève `IntrouvableError` (404) : la distinction
    importe pour le scénario de recette R11.
    """
    charger_tenant(store, tenant_id)
    application = store.obtenir_application(tenant_id, application_id)
    if application is not None:
        return application
    if _appartient_a_un_autre_client(store, tenant_id, application_id):
        raise CloisonnementError(
            "Application d'un autre client",
            tenant_id=tenant_id,
            application_id=application_id,
        )
    raise IntrouvableError(
        f"Application inconnue : {application_id}", application_id=application_id
    )


def _appartient_a_un_autre_client(store: Store, tenant_id: str, application_id: str) -> bool:
    """Vrai si l'application existe chez un autre client."""
    return any(
        autre.id != tenant_id and store.obtenir_application(autre.id, application_id) is not None
        for autre in store.lister_tenants()
    )


def type_tache_de(type_requete: str) -> str:
    """Type de tâche d'un type de requête (EF-EVA-02)."""
    return TYPES_TACHE.get(type_requete.strip().lower(), "general")


def type_tache_d_description(description: str) -> str:
    """Type de tâche d'une application, déduit de sa description."""
    texte = description.lower()
    for mot_cle, type_tache in MOTS_CLES_TACHE:
        if mot_cle in texte:
            return type_tache
    return "general"


def famille_de(modele_id: str) -> str:
    """Famille d'un modèle, déduite de son identifiant."""
    minuscule = modele_id.lower()
    for marqueur, famille in FAMILLES_MODELES:
        if marqueur in minuscule:
            return famille
    return "autre"


def fenetre_contexte_de(modele_id: str) -> int:
    """Fenêtre de contexte d'un modèle (hypothèse de démonstration)."""
    return FENETRE_CONTEXTE_DEFAUT


def rang_capacite(famille: str) -> int:
    """Rang de capacité d'une famille : « plus capable » = rang plus élevé."""
    return {"grand": 3, "moyen": 2, "petit": 1}.get(famille, 1)
