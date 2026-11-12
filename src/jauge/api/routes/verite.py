"""Portes déterministes à vérité terrain (`POST /v1/applications/{id}/verite-terrain`).

Là où une réponse objective existe, le juge n'est pas consulté : le code
produit est exécuté dans le bac à sable, le JSON est validé contre son schéma,
la valeur numérique est comparée à une référence calculée. La route rend le
**verdict**, jamais les textes : ni la réponse, ni la référence, ni le code ne
sont conservés ni journalisés (ENF-CONF-01).

Deux garde-fous s'appliquent, comme pour toute opération qui engage le client :

- le **kill-switch** : un client qui a coupé ses traitements ne voit plus son
  code exécuté, même dans un bac à sable ;
- le **cloisonnement** : la porte ne s'exerce que sur les applications du client
  qui appelle (ENF-SEC-02).

Par défaut, la porte « code » **exige une isolation par le noyau** : sur une
machine qui ne sait pas la tenir, elle ne s'exécute pas et se déclare non
applicable — elle ne prouve rien et ne bloque rien, plutôt que de faire tourner
du code hostile dans un simple sous-processus.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jauge.api.audit import journaliser
from jauge.api.auth import RoleUtilisateur
from jauge.api.deps import DepAudit, DepStore, DepUtilisateur
from jauge.api.killswitch import DepKillSwitch, garde_traitement
from jauge.api.routes.applications import application_controlee
from jauge.api.tenancy import exiger_role
from jauge.evaluation.verite_terrain import (
    TOLERANCE_RELATIVE_DEFAUT,
    AttenduVerite,
    TypePorte,
    evaluer_porte,
)

router = APIRouter(tags=["verite-terrain"])

#: Rôles habilités à faire tourner une porte : le code est exécuté, donc la
#: décision reste celle d'un rôle d'exploitation et non d'un relecteur.
ROLES_VERITE = (
    RoleUtilisateur.ADMINISTRATEUR_PLATEFORME,
    RoleUtilisateur.ANALYSTE_FINOPS,
)


class DemandeVerite(BaseModel):
    """Corps de `POST /v1/applications/{id}/verite-terrain`."""

    porte: TypePorte
    reponse_reference: str = ""
    reponse_candidate: str = ""
    tests: str = ""
    cles_attendues: list[str] = Field(default_factory=list)
    valeur_attendue: float | None = None
    tolerance_relative: float = TOLERANCE_RELATIVE_DEFAUT


class VerdictPorte(BaseModel):
    """Verdict d'une porte : applicable ou non, et pourquoi."""

    porte: TypePorte
    nom: str
    ok: bool
    critique: bool
    applicable: bool
    motif: str


@router.post("/v1/applications/{app_id}/verite-terrain", response_model=VerdictPorte)
def verifier_verite_terrain(
    app_id: str,
    demande: DemandeVerite,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
    kill_switch: DepKillSwitch,
) -> VerdictPorte:
    """Tranche une réponse par la vérité terrain, sans consulter le juge."""
    exiger_role(utilisateur, *ROLES_VERITE)
    application = application_controlee(app_id, utilisateur, store, audit, "verite_terrain")
    # Le code candidat est exécuté : c'est un traitement, le kill-switch s'applique.
    garde_traitement(kill_switch, audit, utilisateur)(application.tenant_id, "verite_terrain")
    attendu = AttenduVerite(
        porte=demande.porte,
        tests=demande.tests,
        cles_attendues=tuple(demande.cles_attendues),
        valeur=demande.valeur_attendue,
        tolerance_relative=demande.tolerance_relative,
    )
    verdict = evaluer_porte(attendu, demande.reponse_reference, demande.reponse_candidate)
    # Journal d'audit sans le moindre texte (ENF-CONF-01) : le nom de la porte
    # et le verdict suffisent à expliquer pourquoi la décision a été prise.
    journaliser(
        audit,
        utilisateur,
        "verite_terrain_evaluee",
        "application",
        application.id,
        {
            "porte": demande.porte.value,
            "ok": verdict.ok,
            "critique": verdict.critique,
            "nom": verdict.nom,
        },
        tenant_id=application.tenant_id,
    )
    return VerdictPorte(
        porte=demande.porte,
        nom=verdict.nom,
        ok=verdict.ok,
        critique=verdict.critique,
        applicable=verdict.critique,
        motif=verdict.motif,
    )
