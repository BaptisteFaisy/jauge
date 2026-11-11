"""Jeu d'essai de démonstration : trois modèles, une campagne, des résultats.

Ce jeu d'essai est partagé par les tests des agents et par la démonstration hors
ligne : il construit un `ServicesFictifs` complet (agrégats de dix jours,
leviers, rapprochement, candidats, campagne terminée avec ses résultats et les
valeurs de recommandation de l'Annexe B).

Tous les nombres sont calculés par le code du socle
(`jauge.engine.calculator`, `jauge.engine.stats`) : aucun n'est fourni par un
modèle (ENF-IA-01). Les identifiants de modèles sont fictifs et ne désignent
aucun modèle réel.
"""

from __future__ import annotations

from datetime import date, timedelta

from jauge.agents.fakes import ServicesFictifs
from jauge.agents.ports import ClusterEchec, EntreesRecommandation, ModeleCandidat
from jauge.engine.stats import economie_mensuelle
from jauge.models.campaign import ResultatCandidat
from jauge.models.cost import AgregatQuotidien, RapprochementJour
from jauge.models.lever import Levier, TypeLevier
from jauge.models.pricing import ServiceTier, TarifModele
from jauge.models.recommendation import MetriquesCout, MetriquesLatence, MetriquesQualite
from jauge.models.tenant import Application, Tenant

MODELE_GRAND = "modele-grand-demo"
MODELE_MOYEN = "modele-moyen-demo"
MODELE_PETIT = "modele-petit-demo"
MODELE_HORS_LISTE = "modele-hors-liste-demo"
REGION = "eu-west-1"

CAMPAGNE = "c-0001"
"""Campagne de rejeu déjà terminée dans le jeu d'essai."""

COUT_AVANT = 0.0041
COUT_APRES = 0.0009
VOLUME_MENSUEL = 310_000

PRIX_DEMO: dict[str, tuple[float, float, float, float]] = {
    MODELE_GRAND: (0.015, 0.075, 0.0075, 0.0375),
    MODELE_MOYEN: (0.003, 0.015, 0.0015, 0.0075),
    MODELE_PETIT: (0.0008, 0.004, 0.0004, 0.002),
}
"""Prix pour 1 000 jetons : entrée, sortie, puis les mêmes au niveau Flex."""


def tarifs_demo() -> list[TarifModele]:
    """Table datée des trois modèles de la démo, en Standard et en Flex."""
    tarifs: list[TarifModele] = []
    for modele_id, (entree, sortie, entree_flex, sortie_flex) in PRIX_DEMO.items():
        for niveau, prix_entree, prix_sortie in (
            (ServiceTier.STANDARD, entree, sortie),
            (ServiceTier.FLEX, entree_flex, sortie_flex),
        ):
            tarifs.append(
                TarifModele(
                    modele_id=modele_id,
                    region=REGION,
                    niveau=niveau,
                    date_effet=date(2026, 1, 1),
                    prix_entree_1k=prix_entree,
                    prix_sortie_1k=prix_sortie,
                )
            )
    return tarifs


def agregats_demo(tenant: Tenant, app: Application) -> list[AgregatQuotidien]:
    """Dix jours d'agrégats dont la dépense augmente sur la seconde moitié."""
    aujourdhui = date.today()
    return [
        AgregatQuotidien(
            tenant_id=tenant.id,
            application_id=app.id,
            equipe=app.equipe,
            environnement=app.environnement,
            modele_id=app.modele_actuel,
            jour=aujourdhui - timedelta(days=9 - index),
            nombre_requetes=1_000 + 100 * index,
            jetons_entree_total=1_200_000,
            jetons_sortie_total=180_000,
            cout_recalcule_usd=round(4.0 + 0.4 * index, 4),
            latence_p50_ms=800,
            latence_p95_ms=2_000,
        )
        for index in range(10)
    ]


def leviers_demo(tenant: Tenant, app: Application) -> list[Levier]:
    """Deux leviers : modèle surdimensionné et cache de prompts absent."""
    return [
        Levier(
            id="lv-0001",
            tenant_id=tenant.id,
            application_id=app.id,
            type_levier=TypeLevier.MODELE_SURDIMENSIONNE,
            action_proposee="Tester un modèle plus petit",
            preuve_exigee="Campagne de rejeu conforme",
            economie_mensuelle_estimee_usd=992.0,
            candidats_suggeres=[MODELE_MOYEN],
        ),
        Levier(
            id="lv-0002",
            tenant_id=tenant.id,
            application_id=app.id,
            type_levier=TypeLevier.CACHE_PROMPTS_ABSENT,
            action_proposee="Activer le cache de prompts",
            preuve_exigee="Mesure du taux de lecture de cache",
            economie_mensuelle_estimee_usd=120.0,
            confiance=0.6,
        ),
    ]


def rapprochements_demo(tenant: Tenant) -> list[RapprochementJour]:
    """Trois jours de rapprochement, sous la cible de 3 % d'écart (§4)."""
    aujourdhui = date.today()
    return [
        RapprochementJour(
            tenant_id=tenant.id,
            jour=aujourdhui - timedelta(days=index),
            cout_recalcule_usd=20.0,
            cout_facture_usd=20.4,
            ecart_usd=-0.4,
            ecart_pourcentage=1.96,
            part_non_attribuee_pourcentage=2.5,
        )
        for index in range(3)
    ]


def resultats_demo() -> list[ResultatCandidat]:
    """Référence, candidat conforme et candidat trop faible (§13.4)."""
    return [
        ResultatCandidat(
            modele_id=MODELE_GRAND,
            est_reference=True,
            nombre_requetes_rejouees=200,
            taux_equivalence=0.985,
            intervalle_confiance_bas=0.958,
            intervalle_confiance_haut=0.995,
            auto_accord_modele_actuel=0.985,
            cout_par_requete_usd=COUT_AVANT,
            latence_p50_ms=820,
            latence_p95_ms=2_100,
            conforme_seuils=True,
        ),
        ResultatCandidat(
            modele_id=MODELE_MOYEN,
            nombre_requetes_rejouees=200,
            taux_equivalence=0.975,
            intervalle_confiance_bas=0.943,
            intervalle_confiance_haut=0.990,
            auto_accord_modele_actuel=0.985,
            accord_juge_humain=0.93,
            cout_par_requete_usd=COUT_APRES,
            latence_p50_ms=410,
            latence_p95_ms=900,
            conforme_seuils=True,
        ),
        ResultatCandidat(
            modele_id=MODELE_PETIT,
            nombre_requetes_rejouees=200,
            taux_equivalence=0.880,
            intervalle_confiance_bas=0.828,
            intervalle_confiance_haut=0.920,
            auto_accord_modele_actuel=0.985,
            echecs_deterministes=4,
            cout_par_requete_usd=0.0002,
            latence_p50_ms=300,
            latence_p95_ms=700,
            familles_en_echec=["tickets en langue rare"],
            conforme_seuils=False,
        ),
    ]


def clusters_demo() -> list[ClusterEchec]:
    """Famille où le petit modèle échoue le plus (étiquette, pas de texte)."""
    return [
        ClusterEchec(
            modele_id=MODELE_PETIT,
            famille="tickets en langue rare",
            nombre_requetes=5,
            nombre_echecs=4,
            taux_echec=0.8,
        )
    ]


def entrees_demo(tenant: Tenant, app: Application) -> EntreesRecommandation:
    """Valeurs calculées de la recommandation (blocs de l'Annexe B)."""
    return EntreesRecommandation(
        tenant_id=tenant.id,
        application_id=app.id,
        campagne_id=CAMPAGNE,
        modele_actuel=MODELE_GRAND,
        modele_cible=MODELE_MOYEN,
        taille_echantillon=200,
        quality=MetriquesQualite(
            equivalenceRate=0.975,
            confidenceIntervalLow=0.943,
            selfAgreementOfCurrentModel=0.985,
            deterministicFailures=0,
            judgeHumanAgreement=0.93,
        ),
        cost=MetriquesCout(
            costPerRequestBefore=COUT_AVANT,
            costPerRequestAfter=COUT_APRES,
            monthlyRequests=VOLUME_MENSUEL,
            expectedMonthlySaving=economie_mensuelle(COUT_AVANT, COUT_APRES, VOLUME_MENSUEL),
        ),
        latency=MetriquesLatence(p50Before=820, p50After=410, p95Before=2_100, p95After=900),
        nombres_contexte=tuple(
            float(nombre)
            for cluster in clusters_demo()
            for nombre in (cluster.nombre_requetes, cluster.nombre_echecs)
        ),
    )


def jeu_demo(tenant: Tenant, applications: list[Application]) -> ServicesFictifs:
    """Construit le jeu d'essai complet pour un client et ses applications.

    La première application est celle de la campagne de rejeu déjà terminée.
    """
    jeu = ServicesFictifs.depuis_fixtures(tenant, applications, tarifs_demo())
    principale = applications[0]
    jeu.remplir_periode(agregats_demo(tenant, principale))
    jeu.remplir_leviers(leviers_demo(tenant, principale))
    jeu.remplir_rapprochements(rapprochements_demo(tenant))
    jeu.candidats_par_app[principale.id] = [
        ModeleCandidat(modele_id=MODELE_MOYEN, famille="moyen", fenetre_contexte=200_000),
        ModeleCandidat(modele_id=MODELE_PETIT, famille="petit", fenetre_contexte=200_000),
        ModeleCandidat(
            modele_id=MODELE_HORS_LISTE,
            famille="hors-liste",
            fenetre_contexte=128_000,
            autorise=False,
        ),
    ]
    jeu.enregistrer_campagne(CAMPAGNE, tenant.id, principale.id)
    jeu.remplir_resultats(
        CAMPAGNE,
        resultats_demo(),
        clusters=clusters_demo(),
        entrees=entrees_demo(tenant, principale),
    )
    return jeu
