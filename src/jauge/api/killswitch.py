"""Kill-switch client : droit d'arrêt immédiat de tout traitement (piste n° 9).

Exigences visées : EF-ADM-01 (paramètres par client), EF-ADM-02 (journal
d'audit), ENF-CONF-01 (le texte reste dans le compte du client), ENF-SEC-02
(cloisonnement).

Le kill-switch est un **droit du client**, pas un outil d'exploitation du MSP :

- un rôle du client (directeur, responsable d'application, relecteur) coupe et
  rétablit le traitement de son propre compte ;
- l'administrateur de plateforme peut aussi agir, **avec un motif journalisé** ;
- un rôle MSP transverse (analyste FinOps, responsable de compte) ne peut
  **jamais** libérer le kill-switch à la place du client.

Une fois coupé, tout traitement du client est refusé en 409 avec le code
explicite ``traitement_suspendu`` : rejeu, jugement, campagne, bascule et
génération de rapport. Les données déjà produites ne sont **jamais** supprimées
par cette bascule : le kill-switch arrête le traitement, il n'efface rien.

Le magasin d'états est local au module API (comme `jauge.api.reglages`) : le
contrat `jauge.store.protocol.Store` est verrouillé et ne définit pas encore de
persistance pour cet état (besoin signalé dans la PR).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Annotated, Protocol

from fastapi import Depends, Request
from pydantic import BaseModel

from jauge.api.audit import AuditSink, journaliser
from jauge.api.auth import RoleUtilisateur, Utilisateur
from jauge.api.tenancy import AccesRefuseError, tenant_autorise
from jauge.common.errors import JaugeError
from jauge.common.ids import horodatage_utc

#: Rôles appartenant au client final (§6) : eux seuls gèrent le kill-switch.
ROLES_CLIENT = frozenset(
    {
        RoleUtilisateur.DIRECTEUR,
        RoleUtilisateur.RESPONSABLE_APPLICATION,
        RoleUtilisateur.RELECTEUR,
    }
)

#: Traitements coupés par le kill-switch, cités dans les refus (traçabilité).
TRAITEMENTS_COUPRES = (
    "rejeu",
    "jugement",
    "campagne",
    "bascule",
    "rapport",
)

#: Actions d'audit du kill-switch (EF-ADM-02).
ACTION_AUDIT_BASCULE = "kill_switch_bascule"
ACTION_AUDIT_REFUS = "kill_switch_refus"
ACTION_AUDIT_TRAITEMENT_REFUSE = "traitement_refuse_kill_switch"

#: Ressource d'audit du kill-switch.
RESSOURCE_KILL_SWITCH = "kill_switch"


class EtatKillSwitch(StrEnum):
    """État du traitement d'un client."""

    ACTIF = "actif"
    SUSPENDU = "suspendu"


class ActionKillSwitch(StrEnum):
    """Action demandée sur le kill-switch (corps de la route `POST`).

    Les valeurs sont en ASCII (sans accent) pour rester stables dans les
    contrats d'API et les journaux.
    """

    ACTIVER = "activer"
    LIBERER = "liberer"


class TraitementSuspenduError(JaugeError):
    """Traitement refusé : le client a coupé son kill-switch (409)."""

    code = "traitement_suspendu"
    statut_http = 409


class EtatKillSwitchClient(BaseModel):
    """État du kill-switch d'un client, tel que rendu par l'API.

    `motif`, `modifie_par` et `modifie_le` répondent à la question « qui a
    coupé, quand, et pourquoi » sans avoir à ouvrir le journal d'audit.
    """

    tenant_id: str
    etat: EtatKillSwitch = EtatKillSwitch.ACTIF
    motif: str | None = None
    modifie_par: str | None = None
    modifie_le: str | None = None


class KillSwitchStore(Protocol):
    """Contrat de persistance des états de kill-switch, par client."""

    def obtenir_etat(self, tenant_id: str) -> EtatKillSwitchClient:
        """Rend l'état courant (actif par défaut, jamais d'erreur)."""
        ...

    def appliquer(
        self,
        tenant_id: str,
        etat: EtatKillSwitch,
        motif: str,
        auteur: str,
        quand: str | None = None,
    ) -> EtatKillSwitchClient:
        """Enregistre un nouvel état et rend l'état appliqué."""
        ...


class MemoryKillSwitchStore:
    """États de kill-switch en mémoire : tests, démo et plan de contrôle local.

    Un verrou protège le dictionnaire : plusieurs requêtes peuvent basculer
    l'état du même client simultanément.
    """

    def __init__(self) -> None:
        self._etats: dict[str, EtatKillSwitchClient] = {}
        self._verrou = Lock()

    def obtenir_etat(self, tenant_id: str) -> EtatKillSwitchClient:
        """Rend l'état du client, actif par défaut (aucun état enregistré)."""
        with self._verrou:
            return self._etats.get(tenant_id) or EtatKillSwitchClient(tenant_id=tenant_id)

    def appliquer(
        self,
        tenant_id: str,
        etat: EtatKillSwitch,
        motif: str,
        auteur: str,
        quand: str | None = None,
    ) -> EtatKillSwitchClient:
        """Écrit l'état demandé, avec son auteur, son motif et son horodatage."""
        nouvel_etat = EtatKillSwitchClient(
            tenant_id=tenant_id,
            etat=etat,
            motif=motif,
            modifie_par=auteur,
            modifie_le=quand or horodatage_utc(datetime.now(UTC)),
        )
        with self._verrou:
            self._etats[tenant_id] = nouvel_etat
        return nouvel_etat


def obtenir_kill_switch(request: Request) -> KillSwitchStore:
    """Rend le magasin de kill-switch de l'application, créé à la demande.

    La création paresseuse évite d'imposer un nouveau champ d'état à la fabrique
    `creer_app` (fichier verrouillé pour ce module) : le magasin est rangé dans
    `app.state.killswitch` au premier appel.
    """
    magasin = getattr(request.app.state, "killswitch", None)
    if magasin is None:
        magasin = MemoryKillSwitchStore()
        request.app.state.killswitch = magasin
    return magasin


#: Alias de dépendance FastAPI du magasin de kill-switch.
DepKillSwitch = Annotated[KillSwitchStore, Depends(obtenir_kill_switch)]


def est_role_client(utilisateur: Utilisateur) -> bool:
    """Vrai si l'utilisateur porte un rôle du client final (hors MSP)."""
    return utilisateur.role in ROLES_CLIENT


def peut_gerer_kill_switch(utilisateur: Utilisateur, tenant_id: str) -> bool:
    """Décide qui peut basculer le kill-switch d'un client.

    Le kill-switch est un droit du client : un rôle du client agit sur son
    propre compte, l'administrateur de plateforme agit aussi (motif journalisé).
    Un rôle MSP transverse n'a **aucun** droit de bascule, en particulier pas
    celui de libérer le kill-switch à la place du client.
    """
    if utilisateur.est_admin:
        return True
    return est_role_client(utilisateur) and tenant_autorise(utilisateur, tenant_id)


def erreur_role_insuffisant(utilisateur: Utilisateur) -> AccesRefuseError:
    """Refus explicite opposé à un rôle qui n'est pas celui du client."""
    return AccesRefuseError(
        "Only a client role or the platform administrator can act on the kill-switch",
        role=utilisateur.role.value,
        roles_clients=[role.value for role in sorted(ROLES_CLIENT)],
    )


def exiger_traitement_autorise(
    kill_switch: KillSwitchStore,
    tenant_id: str,
    action: str = "traitement",
) -> EtatKillSwitchClient:
    """Garde-fou de toute route qui lance un traitement (409 si coupé).

    À appeler **avant** tout travail : rejeu, jugement, lancement de campagne,
    bascule ou génération de rapport. Rendre l'état permet à l'appelant de
    journaliser le motif de la suspension sans seconde lecture.
    """
    etat = kill_switch.obtenir_etat(tenant_id)
    if etat.etat is EtatKillSwitch.SUSPENDU:
        raise TraitementSuspenduError(
            "Processing is suspended for this client",
            tenant_id=tenant_id,
            action=action,
            motif=etat.motif,
            modifie_par=etat.modifie_par,
            modifie_le=etat.modifie_le,
            traitements_coupe=list(TRAITEMENTS_COUPRES),
        )
    return etat


def garde_traitement(
    kill_switch: KillSwitchStore,
    audit: AuditSink | None = None,
    utilisateur: Utilisateur | None = None,
) -> Callable[[str, str], None]:
    """Fabrique un garde-fou réutilisable par les routes qui lancent un traitement.

    Usage côté route (intégration) : ``garde = garde_traitement(kill_switch)``
    puis ``garde(tenant.id, "lancement_campagne")`` avant d'appeler le port.
    Quand un journal d'audit et l'utilisateur sont fournis, le refus est
    également consigné (EF-ADM-02).
    """

    def garde(tenant_id: str, action: str = "traitement") -> None:
        try:
            exiger_traitement_autorise(kill_switch, tenant_id, action)
        except TraitementSuspenduError:
            journaliser_traitement_refuse(audit, utilisateur, tenant_id, action)
            raise

    return garde


def journaliser_bascule(
    audit: AuditSink,
    utilisateur: Utilisateur,
    etat: EtatKillSwitchClient,
) -> None:
    """Consigne la bascule du kill-switch : qui, quand, pourquoi (EF-ADM-02)."""
    journaliser(
        audit,
        utilisateur,
        ACTION_AUDIT_BASCULE,
        RESSOURCE_KILL_SWITCH,
        etat.tenant_id,
        {
            "etat": etat.etat.value,
            "motif": etat.motif,
            "modifie_le": etat.modifie_le,
            "role": utilisateur.role.value,
        },
        tenant_id=etat.tenant_id,
    )


def journaliser_refus_kill_switch(
    audit: AuditSink,
    utilisateur: Utilisateur,
    tenant_id: str,
    action: str,
    motif: str,
    statut_http: int = 403,
) -> None:
    """Consigne une tentative refusée sur le kill-switch (événement de sécurité)."""
    journaliser(
        audit,
        utilisateur,
        ACTION_AUDIT_REFUS,
        RESSOURCE_KILL_SWITCH,
        tenant_id,
        {"action_demandee": action, "motif": motif, "role": utilisateur.role.value},
        tenant_id=tenant_id,
        succes=False,
        statut_http=statut_http,
    )


def journaliser_traitement_refuse(
    audit: AuditSink | None,
    utilisateur: Utilisateur | None,
    tenant_id: str,
    action: str,
) -> None:
    """Consigne le refus d'un traitement parce que le kill-switch est coupé."""
    if audit is None or utilisateur is None:
        return
    journaliser(
        audit,
        utilisateur,
        ACTION_AUDIT_TRAITEMENT_REFUSE,
        RESSOURCE_KILL_SWITCH,
        tenant_id,
        {"action": action, "etat": EtatKillSwitch.SUSPENDU.value},
        tenant_id=tenant_id,
        succes=False,
        statut_http=409,
    )


def etat_cible(action: ActionKillSwitch) -> EtatKillSwitch:
    """Traduit l'action demandée en état cible du kill-switch."""
    if action is ActionKillSwitch.ACTIVER:
        return EtatKillSwitch.SUSPENDU
    return EtatKillSwitch.ACTIF
