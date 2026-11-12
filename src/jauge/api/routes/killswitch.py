"""Routes du kill-switch client et des seuils de confidentialité (piste n° 9).

- `POST /v1/tenants/{id}/kill-switch` : coupe (`activer`) ou rétablit
  (`liberer`) tout traitement du client, avec un **motif obligatoire**.
- `GET /v1/tenants/{id}/kill-switch` : état courant (qui, quand, pourquoi).
- `GET /v1/tenants/{id}/confidentialite` : politique k-anonymité / epsilon.
- `PUT /v1/tenants/{id}/confidentialite` : réglage du seuil k (EF-ADM-01),
  plancher refusé, modification journalisée (EF-ADM-02).

Le kill-switch est un droit du client : un rôle MSP transverse ne peut ni le
couper ni le libérer à la place du client (`jauge.api.killswitch`). Toute
bascule, tout refus et tout réglage de seuil passent par le journal d'audit.

Ce routeur n'est pas encore enregistré dans `jauge.api.routes.ROUTEURS`
(fichier verrouillé pour ce module) : l'intégration est signalée dans la PR.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from jauge.api.audit import journaliser
from jauge.api.auth import RoleUtilisateur
from jauge.api.deps import DepAudit, DepStore, DepUtilisateur
from jauge.api.killswitch import (
    ActionKillSwitch,
    DepKillSwitch,
    EtatKillSwitchClient,
    erreur_role_insuffisant,
    etat_cible,
    journaliser_bascule,
    journaliser_refus_kill_switch,
    peut_gerer_kill_switch,
)
from jauge.api.tenancy import exiger_role, verifier_acces_tenant
from jauge.common.errors import IntrouvableError
from jauge.reports.anonymisation import (
    K_DEFAUT,
    K_PLANCHER,
    MemorySeuilsAnonymiteStore,
    PolitiqueAnonymite,
    SeuilsAnonymiteStore,
)
from jauge.store.protocol import Store

router = APIRouter(tags=["kill-switch"])

#: Actions d'audit des seuils de confidentialité (EF-ADM-01, EF-ADM-02).
ACTION_AUDIT_LECTURE_SEUIL = "lecture_seuil_anonymite"
ACTION_AUDIT_MODIFICATION_SEUIL = "modification_seuil_anonymite"
RESSOURCE_SEUIL = "politique_anonymite"


def obtenir_seuils_anonymite(request: Request) -> SeuilsAnonymiteStore:
    """Rend le magasin de seuils de l'application, créé à la demande.

    Comme pour le kill-switch : la fabrique `creer_app` est verrouillée pour ce
    module, le magasin est donc rangé dans `app.state.seuils_anonymite` au
    premier appel.
    """
    magasin = getattr(request.app.state, "seuils_anonymite", None)
    if magasin is None:
        magasin = MemorySeuilsAnonymiteStore()
        request.app.state.seuils_anonymite = magasin
    return magasin


#: Alias de dépendance FastAPI du magasin de seuils de confidentialité.
DepSeuilsAnonymite = Annotated[SeuilsAnonymiteStore, Depends(obtenir_seuils_anonymite)]


class CommandeKillSwitch(BaseModel):
    """Corps de `POST /v1/tenants/{id}/kill-switch`.

    `action` vaut `activer` (coupe le traitement) ou `liberer` (le rétablit) ;
    `motif` est **obligatoire** et journalisé tel quel (EF-ADM-02).
    """

    action: ActionKillSwitch
    motif: str = Field(min_length=3, max_length=500)

    @field_validator("motif")
    @classmethod
    def _motif_non_blanc(cls, valeur: str) -> str:
        """Un motif vide ou blanc n'est pas un motif."""
        if not valeur.strip():
            raise ValueError("a reason is required to act on the kill-switch")
        return valeur.strip()


class ReglageConfidentialite(BaseModel):
    """Corps de `PUT /v1/tenants/{id}/confidentialite` (EF-ADM-01).

    `k` est le seuil de k-anonymité : jamais sous le plancher `K_PLANCHER`.
    `epsilon` est optionnel : absent, l'epsilon courant est conservé.
    """

    k: int = Field(default=K_DEFAUT, ge=K_PLANCHER, le=1000)
    epsilon: float | None = Field(default=None, gt=0.0, le=100.0)
    motif: str | None = Field(default=None, max_length=500)


def _client_existant(store: Store, tenant_id: str) -> None:
    """Vérifie l'existence du client avant toute action sur ses réglages."""
    if store.obtenir_tenant(tenant_id) is None:
        raise IntrouvableError("Client introuvable", tenant_id=tenant_id)


@router.post("/v1/tenants/{tenant_id}/kill-switch", response_model=EtatKillSwitchClient)
def basculer_kill_switch(
    tenant_id: str,
    commande: CommandeKillSwitch,
    utilisateur: DepUtilisateur,
    store: DepStore,
    kill_switch: DepKillSwitch,
    audit: DepAudit,
) -> EtatKillSwitchClient:
    """Coupe ou rétablit le traitement du client (droit du client, motif requis)."""
    verifier_acces_tenant(utilisateur, tenant_id, audit, "bascule_kill_switch", "tenant")
    _client_existant(store, tenant_id)
    if not peut_gerer_kill_switch(utilisateur, tenant_id):
        journaliser_refus_kill_switch(
            audit, utilisateur, tenant_id, commande.action.value, "role_insuffisant"
        )
        raise erreur_role_insuffisant(utilisateur)
    etat = kill_switch.appliquer(
        tenant_id,
        etat_cible(commande.action),
        commande.motif,
        utilisateur.id,
    )
    journaliser_bascule(audit, utilisateur, etat)
    return etat


@router.get("/v1/tenants/{tenant_id}/kill-switch", response_model=EtatKillSwitchClient)
def lire_kill_switch(
    tenant_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    kill_switch: DepKillSwitch,
    audit: DepAudit,
) -> EtatKillSwitchClient:
    """Rend l'état du kill-switch : actif par défaut, jamais d'erreur."""
    verifier_acces_tenant(utilisateur, tenant_id, audit, "lecture_kill_switch", "tenant")
    _client_existant(store, tenant_id)
    return kill_switch.obtenir_etat(tenant_id)


@router.get("/v1/tenants/{tenant_id}/confidentialite", response_model=PolitiqueAnonymite)
def lire_confidentialite(
    tenant_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    seuils: DepSeuilsAnonymite,
    audit: DepAudit,
) -> PolitiqueAnonymite:
    """Rend la politique k / epsilon du client (défaut documenté si non réglée)."""
    verifier_acces_tenant(utilisateur, tenant_id, audit, ACTION_AUDIT_LECTURE_SEUIL, "tenant")
    _client_existant(store, tenant_id)
    return seuils.obtenir_politique(tenant_id)


@router.put("/v1/tenants/{tenant_id}/confidentialite", response_model=PolitiqueAnonymite)
def regler_confidentialite(
    tenant_id: str,
    reglage: ReglageConfidentialite,
    utilisateur: DepUtilisateur,
    store: DepStore,
    seuils: DepSeuilsAnonymite,
    audit: DepAudit,
) -> PolitiqueAnonymite:
    """Règle le seuil k du client (administrateur), modification journalisée."""
    exiger_role(utilisateur, RoleUtilisateur.ADMINISTRATEUR_PLATEFORME)
    verifier_acces_tenant(utilisateur, tenant_id, audit, ACTION_AUDIT_MODIFICATION_SEUIL, "tenant")
    _client_existant(store, tenant_id)
    politique = seuils.definir_seuil(tenant_id, reglage.k, reglage.epsilon, auteur=utilisateur.id)
    journaliser(
        audit,
        utilisateur,
        ACTION_AUDIT_MODIFICATION_SEUIL,
        RESSOURCE_SEUIL,
        tenant_id,
        {
            "k": politique.k,
            "epsilon": politique.epsilon,
            "plancher_k": politique.plancher_k,
            "motif": reglage.motif,
        },
        tenant_id=tenant_id,
    )
    return politique
