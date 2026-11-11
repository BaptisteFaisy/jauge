"""Amorçage du store de démonstration (§18.1) — alimenté par les modules réels.

`peupler_store` remplit un `jauge.store.memory.MemoryStore` **par le chemin de
production** : plus rien n'est figé dans la démonstration.

1. le trafic synthétique est converti en journaux d'invocation Bedrock ;
2. le module d'attribution (M1) en lit les agrégats et le rapprochement avec
   l'export CUR 2.0 ;
3. le plan de données (textes, requêtes rejouables, profil agrégé) est rempli
   à partir des mêmes journaux ;
4. le détecteur de leviers (M3, les sept règles de l'Annexe A) tourne sur ce
   profil et sur les agrégats ;
5. les campagnes de rejeu (M4) sont exécutées avec le client de modèle hors
   ligne, puis notées par le module d'évaluation (M5) ;
6. les recommandations sont composées par le schéma de l'Annexe B (M7) et
   enregistrées dans le store.

`python -m demo.seed` remplit un store en mémoire et affiche un résumé console
(dépense par application, leviers, économie attendue).

**Aucun appel AWS, aucun modèle de langage** : le parcours est 100 % hors
ligne.

Écart documenté : le scénario « passage au niveau de service Flex » du résumé
nocturne (même modèle, niveau différent) reste décrit par `demo.scenario` :
aucun module ne couvre une campagne multi-niveaux, la campagne de rejeu (M4)
travaillant à un seul niveau (Flex).
"""

from __future__ import annotations

from datetime import date
from functools import cache

from demo.analyse import analyser_trafic
from demo.cur import generer_lignes_cur
from demo.profils import (
    DATE_FIN_DEFAUT,
    GRAINE_DEFAUT,
    JOURS_DEFAUT,
    PROFILS,
)
from demo.scenario import (
    construire_bascules,
    construire_campagnes,
    construire_recommandations,
    construire_verifications,
)
from demo.socle import SocleDemo, charger_socle_demo
from demo.traffic import JournalInvocation, generer_trafic, lignes_cloudwatch
from jauge.agents.schemas import (
    RedactionRecommandation,
    composer_recommandation,
    valider_recommandation,
)
from jauge.integration import (
    Clients,
    PlanDeDonnees,
    ServiceAttribution,
    ServiceDetectionLeviers,
    construire_contexte_levier,
    creer_services,
    remplir_plan_de_donnees,
)
from jauge.integration.leviers import RATIO_SORTIE_MEDIANE_DEMO, parametres_demonstration
from jauge.models.campaign import Campagne, ResultatCandidat
from jauge.models.cost import AgregatQuotidien
from jauge.models.recommendation import StatutRecommandation
from jauge.models.tenant import Application, FormuleOffre, Tenant
from jauge.pricing_data.seed import table_demo
from jauge.store.memory import MemoryStore
from jauge.store.protocol import Store

__all__ = [
    "SocleDemo",
    "charger_socle_demo",
    "comptes_demo",
    "construire_agregats",
    "main",
    "peupler_store",
    "resume",
]

#: Applications dont la campagne de rejeu est exécutée par les modules (M4/M5).
APPLICATIONS_REJEU = ("ticket-classifier", "support-assistant")

#: Application du scénario « niveau de service Flex » (écart documenté).
APPLICATION_FLEX = "doc-summarizer"

#: Taille d'échantillon des campagnes de la démonstration (§13.1).
TAILLE_ECHEANTILLON = 200

#: Graine des campagnes de rejeu (§13.1 : même graine ⇒ même échantillon).
GRAINE_CAMPAGNE = 42

#: Ratio qui définit une sortie longue dans le profil de la démonstration.
RATIO_SORTIE_LONGUE_DEMO = RATIO_SORTIE_MEDIANE_DEMO


@cache
def comptes_demo() -> dict[str, str]:
    """Compte AWS fictif de chaque client, lu dans les fixtures du socle."""
    socle = charger_socle_demo()
    return {
        socle.tenant_a.id: socle.tenant_a.compte_aws,
        socle.tenant_b.id: socle.tenant_b.compte_aws,
    }


def peupler_store(
    store: Store,
    graine: int = GRAINE_DEFAUT,
    jours: int = JOURS_DEFAUT,
    date_fin: date = DATE_FIN_DEFAUT,
) -> PlanDeDonnees:
    """Remplit le store de démonstration par le chemin de production.

    Rend le **plan de données** rempli (textes et requêtes rejouables) : c'est
    lui qu'il faut passer à `jauge.integration.creer_app` pour que l'API puisse
    lancer une campagne de rejeu sur les données de la démonstration.
    """
    socle = charger_socle_demo()
    _enregistrer_referentiel(store, socle)
    table = table_demo()
    plan = PlanDeDonnees()
    services = creer_services(store, mode_demo=True, clients=Clients(), tarifs=table, plan=plan)
    records = generer_trafic(graine=graine, jours=jours, date_fin=date_fin)
    lignes = lignes_cloudwatch(records)
    attribution = ServiceAttribution(store, table)
    lignes_cur = [
        ligne.vers_dictionnaire()
        for ligne in generer_lignes_cur(records, table, graine, comptes_demo())
    ]
    for tenant_id, lignes_tenant in attribution.lignes_par_tenant(lignes).items():
        _attribuer(attribution, tenant_id, lignes_tenant, lignes_cur)
        remplir_plan_de_donnees(
            attribution.enrichir(tenant_id, lignes_tenant),
            tenant_id,
            plan,
            ratio_sortie_longue=RATIO_SORTIE_LONGUE_DEMO,
        )
        _detecter_leviers(store, plan, tenant_id, table, date_fin)
    _campagnes_et_recommandations(store, services, socle)
    _scenario_flex(store, socle, records, table, date_fin, jours)
    return plan


def _enregistrer_referentiel(store: Store, socle: SocleDemo) -> None:
    """Enregistre les deux clients et leurs quatre applications."""
    store.enregistrer_tenant(socle.tenant_a)
    store.enregistrer_tenant(socle.tenant_b)
    for application in socle.applications.values():
        store.enregistrer_application(application)


def _attribuer(
    attribution: ServiceAttribution,
    tenant_id: str,
    lignes: list,
    lignes_cur: list,
) -> None:
    """Agrège les journaux du client et rapproche le recalcul de la facture."""
    resultat = attribution.enregistrer_agregats(tenant_id, lignes)
    attribution.rapprocher(tenant_id, resultat, lignes_cur)


def _detecter_leviers(
    store: Store, plan: PlanDeDonnees, tenant_id: str, table, date_fin: date
) -> None:
    """Fait tourner le détecteur réel (M3) sur chaque application du client.

    Seuls les clients souscrits à une offre d'**optimisation** (§7.1) sont
    analysés : l'offre « visibilité » s'arrête à l'attribution des coûts.
    """
    tenant = store.obtenir_tenant(tenant_id)
    if tenant is None or tenant.formule == FormuleOffre.VISIBILITE:
        return
    detection = ServiceDetectionLeviers(store)
    for application in store.lister_applications(tenant_id):
        profil = plan.profil(tenant_id, application.id)
        if profil is None:
            continue
        contexte = construire_contexte_levier(
            store,
            tenant_id,
            application.id,
            profil,
            parametres_demonstration(date_fin, tenant.region_principale),
            table,
        )
        detection.detecter(contexte)


def _campagnes_et_recommandations(store: Store, services, socle: SocleDemo) -> None:
    """Rejoue les campagnes puis compose les recommandations (M4 → M5 → M7)."""
    for application_id in APPLICATIONS_REJEU:
        application = socle.applications[application_id]
        candidats = [
            candidat.modele_id
            for candidat in services.candidats.modeles_candidats(
                application.tenant_id, application.id
            )
        ]
        services.rejeu.lancer_campagne(
            application.tenant_id,
            application.id,
            candidats,
            TAILLE_ECHEANTILLON,
            GRAINE_CAMPAGNE,
        )
    for application_id in APPLICATIONS_REJEU:
        _recommander(store, services, socle, application_id)


def _recommander(store: Store, services, socle: SocleDemo, application_id: str) -> None:
    """Compose et enregistre la recommandation d'une application (Annexe B)."""
    application = socle.applications[application_id]
    campagne = _campagne_de(store, application)
    entrees = services.entrees.valeurs(application.tenant_id, campagne.id)
    resultat = _resultat_de(campagne, entrees.modele_cible)
    statut = (
        StatutRecommandation.EN_ATTENTE_APPROBATION
        if resultat.conforme_seuils
        else StatutRecommandation.REFUSEE
    )
    redaction = RedactionRecommandation(risks=_risques(resultat), status=statut)
    recommandation = composer_recommandation(entrees, redaction)
    tenant = store.obtenir_tenant(application.tenant_id)
    valider_recommandation(recommandation, tenant.modeles_autorises if tenant else None)
    services.enregistrement.enregistrer(application.tenant_id, recommandation)


def _campagne_de(store: Store, application: Application) -> Campagne:
    """Campagne terminée d'une application."""
    campagnes = store.lister_campagnes(application.tenant_id, app_id=application.id)
    if not campagnes:
        raise ValueError(f"Aucune campagne pour l'application {application.id}")
    return campagnes[-1]


def _resultat_de(campagne: Campagne, modele_id: str) -> ResultatCandidat:
    """Résultat d'un candidat dans une campagne."""
    for resultat in campagne.resultats:
        if resultat.modele_id == modele_id:
            return resultat
    raise ValueError(f"Aucun résultat pour le modèle {modele_id}")


def _risques(resultat: ResultatCandidat) -> list[str]:
    """Risques rédigés pour la recommandation (textes, sans chiffre calculé)."""
    if resultat.echecs_deterministes > 0:
        return [
            "Le candidat échoue aux contrôles déterministes : il ne peut pas être "
            "proposé en l'état.",
            "Surveillance de la qualité en ligne à chaque palier de la bascule.",
        ]
    return [
        "Le taux d'équivalence mesuré reste proche de celui du modèle actuel.",
        "Surveillance de la qualité en ligne à chaque palier de la bascule.",
    ]


def _scenario_flex(
    store: Store,
    socle: SocleDemo,
    records: list[JournalInvocation],
    table,
    date_fin: date,
    jours: int,
) -> None:
    """Scénario « niveau Flex » du résumé nocturne (écart documenté)."""
    stats = analyser_trafic(records, table, PROFILS, jours, date_fin)
    campagnes = [
        campagne
        for campagne in construire_campagnes(stats, table, date_fin)
        if campagne.application_id == APPLICATION_FLEX
    ]
    for campagne in campagnes:
        store.enregistrer_campagne(campagne)
    recommandations = construire_recommandations(stats, campagnes)
    for recommandation in recommandations:
        store.enregistrer_recommandation(recommandation)
    for bascule in construire_bascules(recommandations):
        store.enregistrer_bascule(bascule)
    for verification in construire_verifications(recommandations):
        store.enregistrer_verification(verification)


def construire_agregats(
    records: list[JournalInvocation], socle: SocleDemo, table
) -> list[AgregatQuotidien]:
    """Agrégats quotidiens du trafic, produits par le module d'attribution (M1)."""
    store = MemoryStore()
    _enregistrer_referentiel(store, socle)
    attribution = ServiceAttribution(store, table)
    lignes = lignes_cloudwatch(records)
    agregats: list[AgregatQuotidien] = []
    for tenant_id, lignes_tenant in attribution.lignes_par_tenant(lignes).items():
        agregats.extend(attribution.agreger(tenant_id, lignes_tenant).agregats)
    return agregats


# --- Résumé console -----------------------------------------------------------


def resume(store: Store, jours: int = JOURS_DEFAUT) -> str:
    """Résumé console : dépense par application, leviers, économie attendue."""
    lignes: list[str] = ["Jauge — démonstration hors ligne", ""]
    for tenant in store.lister_tenants():
        lignes.extend(_resume_client(store, tenant, jours))
        lignes.append("")
    lignes.extend(_resume_leviers(store))
    lignes.extend(_resume_decisions(store))
    return "\n".join(lignes)


def _resume_client(store: Store, tenant: Tenant, jours: int) -> list[str]:
    """Tableau de la dépense d'un client, application par application."""
    agregats = store.lister_agregats(tenant.id)
    lignes: list[str] = [f"{tenant.nom} (compte {tenant.compte_aws})"]
    lignes.append(
        f"  {'application':<20} {'requêtes':>9} {'dépense période':>16} "
        f"{'projection/mois':>16} {'leviers':>8}"
    )
    total_requetes = 0
    total_cout = 0.0
    for application in store.lister_applications(tenant.id):
        siens = [a for a in agregats if a.application_id == application.id]
        requetes = sum(a.nombre_requetes for a in siens)
        cout = sum(a.cout_recalcule_usd for a in siens)
        nombre_leviers = len(store.lister_leviers(tenant.id, application.id))
        total_requetes += requetes
        total_cout += cout
        lignes.append(
            f"  {application.id:<20} {requetes:>9} {cout:>15.2f} $ "
            f"{_projection(cout, jours):>15.2f} $ {nombre_leviers:>8}"
        )
    lignes.append(
        f"  {'total':<20} {total_requetes:>9} {total_cout:>15.2f} $ "
        f"{_projection(total_cout, jours):>15.2f} $"
    )
    rapprochements = store.lister_rapprochements(tenant.id)
    if rapprochements:
        conformes = sum(1 for r in rapprochements if r.conforme)
        ecart = sum(r.ecart_pourcentage for r in rapprochements) / len(rapprochements)
        lignes.append(
            f"  rapprochement : écart moyen {ecart:.2f} % — "
            f"{conformes}/{len(rapprochements)} jours conformes (< 3 %)"
        )
    return lignes


def _resume_leviers(store: Store) -> list[str]:
    """Liste des leviers détectés et économie mensuelle estimée."""
    lignes = ["Leviers détectés"]
    total = 0.0
    for tenant in store.lister_tenants():
        for levier in store.lister_leviers(tenant.id):
            total += levier.economie_mensuelle_estimee_usd
            lignes.append(
                f"  {levier.application_id:<20} {levier.type_levier.value:<24} "
                f"{levier.economie_mensuelle_estimee_usd:>8.2f} $/mois  [{levier.statut.value}]"
            )
    lignes.append(f"  {'économie mensuelle estimée':<46} {total:>8.2f} $/mois")
    lignes.append("")
    return lignes


def _resume_decisions(store: Store) -> list[str]:
    """Recommandations par statut et bascule en cours."""
    lignes = ["Décisions"]
    for tenant in store.lister_tenants():
        for reco in store.lister_recommandations(tenant.id):
            lignes.append(
                f"  {reco.applicationId:<20} {reco.status.value:<20} "
                f"économie {reco.cost.expectedMonthlySaving:>8.2f} $/mois "
                f"(cible {reco.targetModel})"
            )
        for bascule in store.lister_bascules(tenant.id):
            lignes.append(
                f"  {bascule.application_id:<20} bascule en cours   "
                f"palier {bascule.palier_actuel_pourcentage} % — "
                f"qualité en ligne {bascule.qualite_en_ligne_mesuree:.3f}"
            )
    return lignes


def _projection(cout: float, jours: int) -> float:
    """Projection mensuelle du coût observé sur la période."""
    return cout * 30.0 / max(1, jours)


def main() -> int:
    """Point d'entrée `python -m demo.seed`."""
    store = MemoryStore()
    peupler_store(store)
    print(resume(store))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
