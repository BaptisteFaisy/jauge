"""Store en mémoire pour les tests, la démo locale et le portail hors-ligne.

Implémente `Store` avec des dictionnaires Python. Aucun appel réseau, aucun
service AWS nécessaire : c'est ce store qu'utilisent les tests d'acceptation
R1–R13 et le mode démo du portail.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from jauge.models.campaign import Campagne
from jauge.models.cost import AgregatQuotidien, RapprochementJour, SyntheseCout
from jauge.models.lever import Levier, StatutLevier
from jauge.models.recommendation import BasculeProgressive, Recommandation, VerificationEconomie
from jauge.models.tenant import Application, Tenant
from jauge.store.synthese import synthese


class MemoryStore:
    """Implémentation en mémoire de tous les contrats du plan de contrôle."""

    def __init__(self) -> None:
        self._tenants: dict[str, Tenant] = {}
        self._apps: dict[str, Application] = {}
        self._agregats: list[AgregatQuotidien] = []
        self._rapprochements: list[RapprochementJour] = []
        self._leviers: dict[str, Levier] = {}
        self._campagnes: dict[str, Campagne] = {}
        self._recos: dict[str, Recommandation] = {}
        self._bascules: dict[str, BasculeProgressive] = {}
        self._verifs: list[VerificationEconomie] = []
        self._app_par_tenant: dict[str, list[str]] = defaultdict(list)

    # --- Tenants et applications -------------------------------------------
    def enregistrer_tenant(self, tenant: Tenant) -> None:
        self._tenants[tenant.id] = tenant

    def obtenir_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def lister_tenants(self) -> list[Tenant]:
        return list(self._tenants.values())

    def enregistrer_application(self, app: Application) -> None:
        self._apps[app.id] = app
        if app.id not in self._app_par_tenant[app.tenant_id]:
            self._app_par_tenant[app.tenant_id].append(app.id)

    def obtenir_application(self, tenant_id: str, app_id: str) -> Application | None:
        app = self._apps.get(app_id)
        # Cloisonnement : on ne rend l'application que si elle appartient au tenant
        return app if app and app.tenant_id == tenant_id else None

    def lister_applications(self, tenant_id: str) -> list[Application]:
        return [self._apps[aid] for aid in self._app_par_tenant.get(tenant_id, [])]

    # --- Agrégats et rapprochement ----------------------------------------
    def enregistrer_agregats(self, agregats: list[AgregatQuotidien]) -> None:
        self._agregats.extend(agregats)

    def lister_agregats(
        self,
        tenant_id: str,
        app_id: str | None = None,
        debut: date | None = None,
        fin: date | None = None,
    ) -> list[AgregatQuotidien]:
        resultat = [a for a in self._agregats if a.tenant_id == tenant_id]
        if app_id:
            resultat = [a for a in resultat if a.application_id == app_id]
        if debut:
            resultat = [a for a in resultat if a.jour >= debut]
        if fin:
            resultat = [a for a in resultat if a.jour <= fin]
        return resultat

    def enregistrer_rapprochement(self, rapprochement: RapprochementJour) -> None:
        self._rapprochements.append(rapprochement)

    def lister_rapprochements(self, tenant_id: str) -> list[RapprochementJour]:
        return [r for r in self._rapprochements if r.tenant_id == tenant_id]

    def synthese_cout(self, tenant_id: str, debut: date, fin: date) -> SyntheseCout:
        """Synthèse de période — calcul partagé avec le store DynamoDB."""
        return synthese(
            tenant_id=tenant_id,
            agregats=self.lister_agregats(tenant_id, debut=debut, fin=fin),
            rapprochements=self._rapprochements,
            applications_connues=[a.id for a in self.lister_applications(tenant_id)],
            debut=debut,
            fin=fin,
        )

    # --- Leviers ------------------------------------------------------------
    def enregistrer_levier(self, levier: Levier) -> None:
        self._leviers[levier.id] = levier

    def lister_leviers(self, tenant_id: str, app_id: str | None = None) -> list[Levier]:
        resultat = [lv for lv in self._leviers.values() if lv.tenant_id == tenant_id]
        if app_id:
            resultat = [lv for lv in resultat if lv.application_id == app_id]
        return resultat

    def maj_statut_levier(self, levier_id: str, statut: str) -> None:
        if levier_id in self._leviers:
            lv = self._leviers[levier_id]
            self._leviers[levier_id] = lv.model_copy(update={"statut": StatutLevier(statut)})

    # --- Campagnes ----------------------------------------------------------
    def enregistrer_campagne(self, campagne: Campagne) -> None:
        self._campagnes[campagne.id] = campagne

    def obtenir_campagne(self, tenant_id: str, campagne_id: str) -> Campagne | None:
        c = self._campagnes.get(campagne_id)
        return c if c and c.tenant_id == tenant_id else None

    def lister_campagnes(self, tenant_id: str, app_id: str | None = None) -> list[Campagne]:
        resultat = [c for c in self._campagnes.values() if c.tenant_id == tenant_id]
        if app_id:
            resultat = [c for c in resultat if c.application_id == app_id]
        return resultat

    # --- Recommandations et bascules ---------------------------------------
    def enregistrer_recommandation(self, reco: Recommandation) -> None:
        self._recos[reco.id] = reco

    def obtenir_recommandation(self, tenant_id: str, reco_id: str) -> Recommandation | None:
        r = self._recos.get(reco_id)
        return r if r and r.tenantId == tenant_id else None

    def lister_recommandations(
        self, tenant_id: str, app_id: str | None = None
    ) -> list[Recommandation]:
        resultat = [r for r in self._recos.values() if r.tenantId == tenant_id]
        if app_id:
            resultat = [r for r in resultat if r.applicationId == app_id]
        return resultat

    def enregistrer_bascule(self, bascule: BasculeProgressive) -> None:
        self._bascules[bascule.id] = bascule

    def obtenir_bascule(self, tenant_id: str, bascule_id: str) -> BasculeProgressive | None:
        b = self._bascules.get(bascule_id)
        return b if b and b.tenant_id == tenant_id else None

    def lister_bascules(
        self, tenant_id: str, app_id: str | None = None
    ) -> list[BasculeProgressive]:
        resultat = [b for b in self._bascules.values() if b.tenant_id == tenant_id]
        if app_id:
            resultat = [b for b in resultat if b.application_id == app_id]
        return resultat

    def enregistrer_verification(self, verif: VerificationEconomie) -> None:
        self._verifs.append(verif)

    def lister_verifications(self, tenant_id: str) -> list[VerificationEconomie]:
        # Les vérifications ne portent pas tenant_id directement ; filtrage par app
        apps = {a.id for a in self.lister_applications(tenant_id)}
        return [v for v in self._verifs if v.application_id in apps]
