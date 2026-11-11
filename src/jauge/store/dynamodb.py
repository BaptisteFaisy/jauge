"""Store DynamoDB du plan de contrôle — implémentation de production de `Store`.

Même contrat que `MemoryStore`, sur la table unique créée par le CDK
(`infra/control-plane/jauge_control_plane/constructs/storage.py`) :

- `PK = TENANT#<tenant_id>` — le client est **dans la clé de partition** : le
  cloisonnement (ENF-SEC-02) est structurel, une requête ne peut pas déborder
  sur un autre client, ce n'est pas un filtre appliqué après coup ;
- `SK = <TYPE>#<identifiant>` — un `begins_with` suffit à lister un type
  d'entités d'un client, jamais de scan de table ;
- `GSI1PK/GSI1SK` (index `par-type`) — vue de parc du MSP et recherche d'une
  entité par son seul identifiant (`maj_statut_levier`) ;
- `REGISTRY#…` — les deux entités dont le modèle ne porte pas de `tenant_id`
  (la liste des clients, les vérifications d'économie).

Le store ne parle jamais à boto3 : il passe par le port `TableEtat`
(`jauge.store.table`), dont `TableBoto3` est l'adaptateur réel. Les tests
utilisent le double en mémoire `tests/store/fake_dynamo.py` — aucun réseau.

Différence assumée avec `MemoryStore` : l'écriture est **idempotente** (mêmes
clés ⇒ remplacement). Un agrégat quotidien réécrit pour le même
(client, application, modèle, jour) remplace le précédent au lieu de s'empiler —
c'est le grain du §14, et rejouer une journée d'attribution ne double pas les
coûts. `tests/store/test_dynamodb.py` vérifie cette propriété, et un test de
parité rejoue le même scénario sur les deux stores.
"""

from __future__ import annotations

from datetime import date
from typing import Any, TypeVar

from jauge.models.campaign import Campagne
from jauge.models.cost import AgregatQuotidien, RapprochementJour, SyntheseCout
from jauge.models.lever import Levier, StatutLevier
from jauge.models.recommendation import BasculeProgressive, Recommandation, VerificationEconomie
from jauge.models.tenant import Application, Tenant
from jauge.store import cles
from jauge.store.synthese import synthese
from jauge.store.table import TableEtat

Entite = TypeVar("Entite")


class DynamoDbStore:
    """`Store` sur DynamoDB : une table, une partition par client."""

    def __init__(self, table: TableEtat) -> None:
        self._table = table

    # --- primitives ----------------------------------------------------------
    def _ecrire(
        self,
        partition: str,
        cle: str,
        type_entite: str,
        entite: Any,
        tenant_id: str = "",
        **attributs: Any,
    ) -> None:
        """Écrit une entité (document JSON + attributs plats d'indexation)."""
        self._table.ecrire(
            cles.vers_item(
                partition,
                cle,
                type_entite,
                entite,
                tenant_id=tenant_id,
                attributs=attributs,
            )
        )

    def _lire(self, partition: str, cle: str, modele: type[Entite]) -> Entite | None:
        """Lit une entité par ses deux clés, ou `None` si elle est absente."""
        return cles.depuis_item(self._table.lire(partition, cle), modele)

    def _lister(self, partition: str, prefixe: str, modele: type[Entite]) -> list[Entite]:
        """Toutes les entités d'un type dans une partition (préfixe de `SK`)."""
        items = self._table.requeter(partition, prefixe)
        entites = [cles.depuis_item(item, modele) for item in items]
        return [entite for entite in entites if entite is not None]

    # --- clients -------------------------------------------------------------
    def enregistrer_tenant(self, tenant: Tenant) -> None:
        """Enregistre un client dans le registre transverse."""
        self._ecrire(
            cles.partition_registre(cles.REGISTRE_TENANTS),
            cles.cle_tri(cles.TENANT, tenant.id),
            cles.TENANT,
            tenant,
            tenant_id=tenant.id,
        )

    def obtenir_tenant(self, tenant_id: str) -> Tenant | None:
        """Rend un client par son identifiant, ou `None`."""
        return self._lire(
            cles.partition_registre(cles.REGISTRE_TENANTS),
            cles.cle_tri(cles.TENANT, tenant_id),
            Tenant,
        )

    def lister_tenants(self) -> list[Tenant]:
        """Tous les clients enregistrés (registre transverse)."""
        return self._lister(
            cles.partition_registre(cles.REGISTRE_TENANTS), cles.cle_tri(cles.TENANT), Tenant
        )

    # --- applications --------------------------------------------------------
    def enregistrer_application(self, app: Application) -> None:
        """Enregistre une application dans la partition de son client."""
        self._ecrire(
            cles.partition_client(app.tenant_id),
            cles.cle_tri(cles.APPLICATION, app.id),
            cles.APPLICATION,
            app,
            tenant_id=app.tenant_id,
            app_id=app.id,
        )

    def obtenir_application(self, tenant_id: str, app_id: str) -> Application | None:
        """Application du client demandé — `None` si elle appartient à un autre."""
        return self._lire(
            cles.partition_client(tenant_id), cles.cle_tri(cles.APPLICATION, app_id), Application
        )

    def lister_applications(self, tenant_id: str) -> list[Application]:
        """Applications d'un client (une seule partition lue)."""
        return self._lister(
            cles.partition_client(tenant_id), cles.cle_tri(cles.APPLICATION), Application
        )

    # --- agrégats et rapprochement ---------------------------------------------
    def enregistrer_agregats(self, agregats: list[AgregatQuotidien]) -> None:
        """Enregistre des agrégats quotidiens (mêmes clés ⇒ remplacement)."""
        for agregat in agregats:
            jour = cles.jour_iso(agregat.jour)
            self._ecrire(
                cles.partition_client(agregat.tenant_id),
                cles.cle_tri(cles.AGREGAT, agregat.application_id, agregat.modele_id, jour),
                cles.AGREGAT,
                agregat,
                tenant_id=agregat.tenant_id,
                app_id=agregat.application_id,
                modele_id=agregat.modele_id,
                jour=jour,
            )

    def lister_agregats(
        self,
        tenant_id: str,
        app_id: str | None = None,
        debut: date | None = None,
        fin: date | None = None,
    ) -> list[AgregatQuotidien]:
        """Agrégats d'un client, filtrés par application puis par période."""
        prefixe = cles.cle_tri(cles.AGREGAT, app_id) if app_id else cles.cle_tri(cles.AGREGAT)
        agregats = self._lister(cles.partition_client(tenant_id), prefixe, AgregatQuotidien)
        return [a for a in agregats if _dans_periode(a.jour, debut, fin)]

    def enregistrer_rapprochement(self, rapprochement: RapprochementJour) -> None:
        """Enregistre le rapprochement quotidien d'un client avec sa facture."""
        self._ecrire(
            cles.partition_client(rapprochement.tenant_id),
            cles.cle_tri(cles.RAPPROCHEMENT, cles.jour_iso(rapprochement.jour)),
            cles.RAPPROCHEMENT,
            rapprochement,
            tenant_id=rapprochement.tenant_id,
            jour=cles.jour_iso(rapprochement.jour),
        )

    def lister_rapprochements(self, tenant_id: str) -> list[RapprochementJour]:
        """Rapprochements d'un client, du plus ancien au plus récent."""
        return self._lister(
            cles.partition_client(tenant_id), cles.cle_tri(cles.RAPPROCHEMENT), RapprochementJour
        )

    def synthese_cout(self, tenant_id: str, debut: date, fin: date) -> SyntheseCout:
        """Synthèse de période — même calcul que le store mémoire (mutualisé)."""
        return synthese(
            tenant_id=tenant_id,
            agregats=self.lister_agregats(tenant_id, debut=debut, fin=fin),
            rapprochements=self.lister_rapprochements(tenant_id),
            applications_connues=[a.id for a in self.lister_applications(tenant_id)],
            debut=debut,
            fin=fin,
        )

    # --- leviers ---------------------------------------------------------------
    def enregistrer_levier(self, levier: Levier) -> None:
        """Enregistre un levier dans la partition de son client."""
        self._ecrire(
            cles.partition_client(levier.tenant_id),
            cles.cle_tri(cles.LEVIER, levier.id),
            cles.LEVIER,
            levier,
            tenant_id=levier.tenant_id,
            app_id=levier.application_id,
        )

    def lister_leviers(self, tenant_id: str, app_id: str | None = None) -> list[Levier]:
        """Leviers d'un client, éventuellement filtrés par application."""
        leviers = self._lister(cles.partition_client(tenant_id), cles.cle_tri(cles.LEVIER), Levier)
        return [lv for lv in leviers if app_id is None or lv.application_id == app_id]

    def maj_statut_levier(self, levier_id: str, statut: str) -> None:
        """Change le statut d'un levier connu par son seul identifiant.

        Le contrat ne donne pas le client : l'index `par-type` (`GSI1PK =
        TYPE#LEVER`) est interrogé, sa projection KEYS_ONLY rend `PK`/`SK`, ce
        qui suffit à relire l'item puis à le réécrire. Aucune entité trouvée ⇒
        aucun effet (même comportement que le store mémoire).
        """
        cible = cles.cle_tri(cles.LEVIER, levier_id)
        for cles_index in self._table.requeter_index(f"TYPE#{cles.LEVIER}"):
            if cles_index.get("SK") != cible:
                continue
            levier = self._lire(str(cles_index["PK"]), cible, Levier)
            if levier is not None:
                self.enregistrer_levier(levier.model_copy(update={"statut": StatutLevier(statut)}))
            return

    # --- campagnes ---------------------------------------------------------------
    def enregistrer_campagne(self, campagne: Campagne) -> None:
        """Enregistre une campagne de rejeu dans la partition de son client."""
        self._ecrire(
            cles.partition_client(campagne.tenant_id),
            cles.cle_tri(cles.CAMPAGNE, campagne.id),
            cles.CAMPAGNE,
            campagne,
            tenant_id=campagne.tenant_id,
            app_id=campagne.application_id,
        )

    def obtenir_campagne(self, tenant_id: str, campagne_id: str) -> Campagne | None:
        """Campagne du client demandé — `None` si elle appartient à un autre."""
        return self._lire(
            cles.partition_client(tenant_id), cles.cle_tri(cles.CAMPAGNE, campagne_id), Campagne
        )

    def lister_campagnes(self, tenant_id: str, app_id: str | None = None) -> list[Campagne]:
        """Campagnes d'un client, éventuellement filtrées par application."""
        campagnes = self._lister(
            cles.partition_client(tenant_id), cles.cle_tri(cles.CAMPAGNE), Campagne
        )
        return [c for c in campagnes if app_id is None or c.application_id == app_id]

    # --- recommandations et bascules ------------------------------------------------
    def enregistrer_recommandation(self, reco: Recommandation) -> None:
        """Enregistre une recommandation dans la partition de son client."""
        self._ecrire(
            cles.partition_client(reco.tenantId),
            cles.cle_tri(cles.RECOMMANDATION, reco.id),
            cles.RECOMMANDATION,
            reco,
            tenant_id=reco.tenantId,
            app_id=reco.applicationId,
        )

    def obtenir_recommandation(self, tenant_id: str, reco_id: str) -> Recommandation | None:
        """Recommandation du client demandé — `None` si elle appartient à un autre."""
        return self._lire(
            cles.partition_client(tenant_id),
            cles.cle_tri(cles.RECOMMANDATION, reco_id),
            Recommandation,
        )

    def lister_recommandations(
        self, tenant_id: str, app_id: str | None = None
    ) -> list[Recommandation]:
        """Recommandations d'un client, éventuellement filtrées par application."""
        recos = self._lister(
            cles.partition_client(tenant_id), cles.cle_tri(cles.RECOMMANDATION), Recommandation
        )
        return [r for r in recos if app_id is None or r.applicationId == app_id]

    def enregistrer_bascule(self, bascule: BasculeProgressive) -> None:
        """Enregistre une bascule dans la partition de son client."""
        self._ecrire(
            cles.partition_client(bascule.tenant_id),
            cles.cle_tri(cles.BASCULE, bascule.id),
            cles.BASCULE,
            bascule,
            tenant_id=bascule.tenant_id,
            app_id=bascule.application_id,
        )

    def obtenir_bascule(self, tenant_id: str, bascule_id: str) -> BasculeProgressive | None:
        """Bascule du client demandé — `None` si elle appartient à un autre."""
        return self._lire(
            cles.partition_client(tenant_id),
            cles.cle_tri(cles.BASCULE, bascule_id),
            BasculeProgressive,
        )

    def lister_bascules(
        self, tenant_id: str, app_id: str | None = None
    ) -> list[BasculeProgressive]:
        """Bascules d'un client, éventuellement filtrées par application."""
        bascules = self._lister(
            cles.partition_client(tenant_id), cles.cle_tri(cles.BASCULE), BasculeProgressive
        )
        return [b for b in bascules if app_id is None or b.application_id == app_id]

    # --- vérifications d'économie ---------------------------------------------------
    def enregistrer_verification(self, verif: VerificationEconomie) -> None:
        """Enregistre une vérification d'économie dans le registre transverse.

        `VerificationEconomie` ne porte pas de `tenant_id` : l'item est rangé
        dans le registre des vérifications, puis filtré par client à la lecture —
        même sémantique que le store mémoire.
        """
        self._ecrire(
            cles.partition_registre(cles.REGISTRE_VERIFICATIONS),
            cles.cle_tri(cles.VERIFICATION, verif.application_id, verif.recommandation_id),
            cles.VERIFICATION,
            verif,
            app_id=verif.application_id,
        )

    def lister_verifications(self, tenant_id: str) -> list[VerificationEconomie]:
        """Vérifications d'économie des applications du client demandé."""
        connues = {a.id for a in self.lister_applications(tenant_id)}
        verifications = self._lister(
            cles.partition_registre(cles.REGISTRE_VERIFICATIONS),
            cles.cle_tri(cles.VERIFICATION),
            VerificationEconomie,
        )
        return [v for v in verifications if v.application_id in connues]


def _dans_periode(jour: date, debut: date | None, fin: date | None) -> bool:
    """Vrai si le jour est dans la période demandée (bornes incluses)."""
    if debut is not None and jour < debut:
        return False
    return not (fin is not None and jour > fin)
