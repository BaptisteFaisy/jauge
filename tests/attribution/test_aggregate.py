"""Tests des agrégats quotidiens (EF-ATT-05, §8.2).

Les agrégats sont la base de tout le reste du produit : leviers (M3),
recommandations (M5), rapports (M9). Ces tests vérifient les dimensions
(application, équipe, environnement, modèle, jour), le respect du calcul de coût
du socle, l'application par défaut et le cloisonnement entre clients.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from tests.attribution.conftest import JOUR, JOURNAUX, MODELE_GRAND, MODELE_PETIT

from jauge.attribution.aggregate import (
    AgregateurQuotidien,
    Colonnes,
    LignesRejouees,
    VueMetriques,
    agregats_par_dimension,
    iterer_par_jour,
)
from jauge.attribution.logs import EnregistrementInvocation, RapportLecture, lire_journaux
from jauge.attribution.tags import Etiquettes
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.models.pricing import MetriquesRequete, ServiceTier
from jauge.models.tenant import Application, Tenant


@pytest.fixture
def resultat(tenant: Tenant, tarifs_demo: TableTarifs, applications: list[Application]):
    """Agrégat du journal de référence, calculé une fois pour tous les tests."""
    rapport = RapportLecture()
    agregateur = AgregateurQuotidien(tenant, tarifs_demo, applications=applications)
    return agregateur.agreger(lire_journaux(JOURNAUX, rapport), rapport)


def test_agregation_par_application_modele_et_jour(resultat) -> None:
    """Chaque couple (application, modèle, jour) produit un agrégat distinct."""
    cles = {(a.application_id, a.modele_id, a.jour) for a in resultat.agregats}

    assert cles == {
        ("ticket-classifier", MODELE_GRAND, JOUR),
        ("support-assistant", MODELE_GRAND, JOUR),
        ("resume-nocturne", MODELE_PETIT, JOUR),
        ("non-attribue", MODELE_PETIT, JOUR),
    }
    assert resultat.nb_enregistrements == 6


def test_dimensions_equipe_et_environnement_remplies(resultat) -> None:
    """Équipe et environnement viennent de la fiche d'application (EF-ATT-05)."""
    par_application = {a.application_id: a for a in resultat.agregats}

    assert par_application["ticket-classifier"].equipe == "support"
    assert par_application["ticket-classifier"].environnement == "production"
    assert par_application["resume-nocturne"].equipe == "data"
    assert par_application["resume-nocturne"].environnement == "staging"


def test_compteurs_de_jetons_consolides(resultat) -> None:
    """Les jetons sont sommés par application, cache inclus (§8.2)."""
    classement = next(a for a in resultat.agregats if a.application_id == "ticket-classifier")

    # req-0001 (1200/80) + req-0005 (500/30) + req-0006 (250/15)
    assert classement.nombre_requetes == 3
    assert classement.jetons_entree_total == 1950
    assert classement.jetons_sortie_total == 125


def test_jetons_de_cache_agreges(tenant: Tenant, tarifs_demo: TableTarifs) -> None:
    """Les jetons de cache-lecture et de cache-écriture sont conservés."""
    agregateur = AgregateurQuotidien(tenant, tarifs_demo)
    enregistrement = EnregistrementInvocation(
        request_id="req-cache",
        horodatage=datetime(2026, 11, 9, 9, 0, tzinfo=UTC),
        model_id=MODELE_GRAND,
        metriques=MetriquesRequete(
            jetons_entree=1000,
            jetons_sortie=100,
            jetons_cache_lecture=400,
            jetons_cache_ecriture=200,
        ),
        etiquettes=Etiquettes(application="ticket-classifier"),
    )

    resultat = agregateur.agreger([enregistrement])

    assert resultat.agregats[0].jetons_cache_lecture_total == 400
    assert resultat.agregats[0].jetons_cache_ecriture_total == 200


def test_cout_identique_au_calcul_du_socle(
    tenant: Tenant, tarifs_demo: TableTarifs, applications: list[Application]
) -> None:
    """Le coût agrégé est la somme exacte des coûts calculés par le socle.

    Aucun calcul parallèle n'est toléré : si ce test casse, une formule de coût
    a été dupliquée (ENF-IA-01).
    """
    agregateur = AgregateurQuotidien(tenant, tarifs_demo, applications=applications)
    resultat = agregateur.agreger(lire_journaux(JOURNAUX))

    attendu = 0.0
    for enregistrement in lire_journaux(JOURNAUX):
        tarif = tarifs_demo.trouver(
            enregistrement.model_id, tenant.region_principale, enregistrement.niveau_service, JOUR
        )
        attendu += calculer_cout_requete(tarif, enregistrement.metriques).cout_total

    assert resultat.cout_recalcule_usd == pytest.approx(round(attendu, 4), abs=1e-4)


def test_niveau_de_service_change_le_cout(tenant: Tenant, tarifs_demo: TableTarifs) -> None:
    """Deux appels identiques, l'un Flex et l'autre Standard, ne coûtent pas pareil."""
    horodatage = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    commun = {
        "horodatage": horodatage,
        "model_id": MODELE_GRAND,
        "metriques": MetriquesRequete(jetons_entree=1000, jetons_sortie=100),
        "etiquettes": Etiquettes(application="ticket-classifier"),
    }
    agregateur = AgregateurQuotidien(tenant, tarifs_demo)

    standard = agregateur.agreger(
        [EnregistrementInvocation(request_id="s", niveau_service=ServiceTier.STANDARD, **commun)]
    )
    flex = agregateur.agreger(
        [EnregistrementInvocation(request_id="f", niveau_service=ServiceTier.FLEX, **commun)]
    )

    assert flex.cout_recalcule_usd < standard.cout_recalcule_usd


def test_appel_non_etiquete_rattache_a_l_application_par_defaut(resultat) -> None:
    """Un appel sans étiquette apparaît sous l'application par défaut (EF-ATT-03)."""
    defaut = next(a for a in resultat.agregats if a.application_id == "non-attribue")

    assert defaut.nombre_requetes == 1
    assert resultat.nb_sans_etiquette == 1


def test_part_non_attribuee_du_jour(resultat) -> None:
    """La part non attribuée du jour est disponible seule (EF-ATT-07)."""
    part = resultat.part_non_attribuee_par_jour[JOUR]

    assert part.depense_non_attribuee_usd == pytest.approx(resultat.cout_non_attribue_usd)
    assert 0 < part.part_pourcentage < 100
    assert part.libelles()  # au moins une cause nommée


def test_application_hors_schema_signalee(
    tenant: Tenant,
    tarifs_demo: TableTarifs,
    applications: list[Application],
    schema_attribution,
) -> None:
    """Une application inconnue du plan de contrôle produit un agrégat signalé."""
    enregistrement = EnregistrementInvocation(
        request_id="req-hors-schema",
        horodatage=datetime(2026, 11, 9, 9, 0, tzinfo=UTC),
        model_id=MODELE_GRAND,
        metriques=MetriquesRequete(jetons_entree=100, jetons_sortie=10),
        etiquettes=Etiquettes(application="app-fantome", equipe="marketing"),
    )
    agregateur = AgregateurQuotidien(
        tenant, tarifs_demo, schema=schema_attribution, applications=applications
    )

    resultat = agregateur.agreger([enregistrement])

    assert resultat.agregats[0].application_id == "app-fantome"
    assert resultat.part_non_attribuee.depense_non_attribuee_usd > 0


def test_requete_sensible_comptee_dans_le_cout(
    tenant: Tenant, tarifs_demo: TableTarifs, applications: list[Application]
) -> None:
    """Une requête sensible est facturée : elle reste dans l'agrégat par défaut."""
    rapport = RapportLecture()
    agregateur = AgregateurQuotidien(tenant, tarifs_demo, applications=applications)

    resultat = agregateur.agreger(lire_journaux(JOURNAUX, rapport), rapport)

    assert rapport.sensibles == 1
    assert resultat.nb_sensibles_lus == 1
    assert resultat.nb_sensibles_exclus == 0
    assert resultat.nb_enregistrements == 6  # sensible comprise : elle est facturée


def test_requete_sensible_exclue_sur_demande(
    tenant: Tenant, tarifs_demo: TableTarifs, applications: list[Application]
) -> None:
    """Le client peut tenir ses requêtes sensibles hors de ses agrégats."""
    agregateur = AgregateurQuotidien(tenant, tarifs_demo, applications=applications)

    resultat = agregateur.agreger(lire_journaux(JOURNAUX), exclure_sensibles=True)

    assert resultat.nb_enregistrements == 5
    assert resultat.nb_sensibles_exclus == 1
    classement = next(a for a in resultat.agregats if a.application_id == "ticket-classifier")
    assert classement.nombre_requetes == 2


def test_agregation_vide() -> None:
    """Aucun enregistrement : aucun agrégat, aucune erreur."""
    agregateur = AgregateurQuotidien(
        Tenant(
            id="t-vide",
            nom="Vide",
            compte_aws="123456789012",
            role_arn="arn:aws:iam::123456789012:role/x",
            external_id="e",
        ),
        TableTarifs(),
    )

    resultat = agregateur.agreger([])

    assert resultat.agregats == []
    assert resultat.cout_recalcule_usd == 0.0
    assert resultat.part_non_attribuee.part_pourcentage == 0.0


def test_cloisonnement_fiches_d_un_autre_client_ignorees(
    tenant: Tenant, tenant_b: Tenant, tarifs_demo: TableTarifs
) -> None:
    """Une fiche d'application d'un autre client n'est jamais utilisée (ENF-SEC-02)."""
    intrusion = Application(
        id="ticket-classifier",
        tenant_id=tenant_b.id,
        nom="Fiche d'un autre client",
        equipe="equipe-b",
        environnement="production",
        modele_actuel=MODELE_GRAND,
    )
    agregateur = AgregateurQuotidien(tenant, tarifs_demo, applications=[intrusion])

    resultat = agregateur.agreger(lire_journaux(JOURNAUX))

    equipes = {a.equipe for a in resultat.agregats if a.application_id == "ticket-classifier"}
    assert "equipe-b" not in equipes
    assert all(a.tenant_id == tenant.id for a in resultat.agregats)


def test_vues_legeres_compatibles_avec_les_modeles_du_socle() -> None:
    """Les vues paresseuses exposent exactement les champs du modèle du socle.

    Garantit que l'optimisation de flux (ENF-PERF-01) ne dérive pas du contrat
    `MetriquesRequete` : si le socle ajoute un champ, ce test le dit.
    """
    assert set(VueMetriques._fields) == set(MetriquesRequete.model_fields)
    assert Colonnes.vides().cout.typecode == "d"
    assert len(LignesRejouees(Colonnes.vides())) == 0


def test_agregats_par_dimension(resultat) -> None:
    """Les totaux par application, équipe, environnement et modèle sont exacts."""
    par_application = agregats_par_dimension(resultat.agregats, "application")
    par_equipe = agregats_par_dimension(resultat.agregats, "equipe")
    par_modele = agregats_par_dimension(resultat.agregats, "modele")

    assert par_application["ticket-classifier"] > par_application["resume-nocturne"]
    assert set(par_equipe) == {"support", "data", "inconnue"}
    assert set(par_modele) == {MODELE_GRAND, MODELE_PETIT}
    assert sum(par_application.values()) == pytest.approx(resultat.cout_recalcule_usd, abs=1e-3)


def test_dimension_inconnue_refusee(resultat) -> None:
    """Une dimension non prévue est refusée explicitement."""
    with pytest.raises(ValueError, match="dimension inconnue"):
        agregats_par_dimension(resultat.agregats, "region")


def test_iteration_par_jour_chronologique(resultat) -> None:
    """Les agrégats se regroupent par jour, du plus ancien au plus récent."""
    jours = [jour for jour, _ in iterer_par_jour(resultat.agregats)]

    assert jours == [JOUR]


def test_resultat_pour_jour_absent(resultat) -> None:
    """Un jour sans agrégat rend `None`, pas une erreur."""
    assert resultat.pour_jour(date(2026, 1, 1)) is None
