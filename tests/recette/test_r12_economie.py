"""Recette R12 — économie après bascule, à volume constant (§22, EF-VER-01).

Scénario : « Économie après bascule » → **coût par requête avant et après, à
volume constant, cohérent avec la facture**.

La recette construit les deux périodes à partir du **même** trafic de
démonstration : mêmes requêtes, donc même volume ; seul le modèle facturé
change (grand modèle avant, petit modèle après). Le module de vérification
(`jauge.rollout.verify`) calcule alors l'économie constatée, et la confirmation
par étiquette (`confirmer_par_etiquette`) la rapproche des dollars facturés lus
dans l'export CUR 2.0.

Aucun réseau, aucun AWS ; les chiffres viennent du moteur de coût du socle.
"""

from __future__ import annotations

import pytest
from demo.cur import generer_lignes_cur
from demo.profils import DATE_FIN_DEFAUT, GRAINE_DEFAUT
from demo.seed import peupler_store
from demo.socle import SocleDemo, charger_socle_demo
from demo.traffic import JournalInvocation, generer_trafic
from fastapi.testclient import TestClient
from tests.conftest import GRAND_MODELE, PETIT_MODELE, REGION
from tests.recette.conftest import entetes

from jauge.engine.attribution import agreger_requetes
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.engine.stats import economie_mensuelle
from jauge.models.cost import AgregatQuotidien
from jauge.models.pricing import ServiceTier
from jauge.models.recommendation import (
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
)
from jauge.models.tenant import Application
from jauge.rollout.verify import confirmer_par_etiquette, cout_par_requete, verifier_economie
from jauge.store.memory import MemoryStore

APP_CLASSEMENT = "ticket-classifier"


def _records_classement() -> list[JournalInvocation]:
    """Trafic synthétique du classement de tickets pour une journée."""
    return [
        record
        for record in generer_trafic(graine=GRAINE_DEFAUT, jours=1, date_fin=DATE_FIN_DEFAUT)
        if record.application_id == APP_CLASSEMENT
    ]


def _periodes(
    socle: SocleDemo, table: TableTarifs
) -> tuple[AgregatQuotidien, AgregatQuotidien, int]:
    """Agrégats avant/après bascule et volume, calculés sur le même trafic."""
    jour = DATE_FIN_DEFAUT
    records = _records_classement()
    application: Application = socle.applications[APP_CLASSEMENT]
    tarif_avant = table.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, jour)
    tarif_apres = table.trouver(PETIT_MODELE, REGION, ServiceTier.STANDARD, jour)
    couples_avant = [
        (record.metriques(), calculer_cout_requete(tarif_avant, record.metriques()))
        for record in records
    ]
    couples_apres = [
        (record.metriques(), calculer_cout_requete(tarif_apres, record.metriques()))
        for record in records
    ]
    avant = agreger_requetes(socle.tenant_a, application, jour, couples_avant)
    apres = agreger_requetes(socle.tenant_a, application, jour, couples_apres).model_copy(
        update={"modele_id": PETIT_MODELE}
    )
    return avant, apres, len(records)


def test_r12_economie_apres_bascule_a_volume_constant() -> None:
    """R12 : le coût par requête avant/après est comparé à volume constant (EF-VER-01)."""
    socle = charger_socle_demo()
    table = TableTarifs(socle.tarifs)
    avant, apres, volume = _periodes(socle, table)
    application: Application = socle.applications[APP_CLASSEMENT]

    # 1. Les deux périodes portent le même volume : c'est la base commune.
    assert avant.nombre_requetes == apres.nombre_requetes == volume

    cout_avant = cout_par_requete([avant], socle.tenant_a.id)
    cout_apres = cout_par_requete([apres], socle.tenant_a.id)
    assert cout_apres < cout_avant, "le petit modèle doit coûter moins cher par requête"

    recommandation = Recommandation(
        id="r-recette-economie",
        tenantId=socle.tenant_a.id,
        applicationId=APP_CLASSEMENT,
        currentModel=GRAND_MODELE,
        targetModel=PETIT_MODELE,
        campaignId="c-recette-economie",
        sampleSize=200,
        quality=MetriquesQualite(
            equivalenceRate=0.97, confidenceIntervalLow=0.93, selfAgreementOfCurrentModel=0.99
        ),
        cost=MetriquesCout(
            costPerRequestBefore=cout_avant,
            costPerRequestAfter=cout_apres,
            monthlyRequests=volume,
            expectedMonthlySaving=round(economie_mensuelle(cout_avant, cout_apres, volume), 4),
        ),
        latency=MetriquesLatence(p50Before=700, p50After=250, p95Before=1400, p95After=600),
    )

    verification = verifier_economie(
        tenant_id=socle.tenant_a.id,
        application=application,
        recommandation=recommandation,
        agregats_avant=[avant],
        agregats_apres=[apres],
    )

    # 2. L'économie constatée est cohérente avec l'économie attendue.
    assert verification.volume_periode == volume
    assert verification.cout_par_requete_avant == pytest.approx(cout_avant)
    assert verification.cout_par_requete_apres == pytest.approx(cout_apres)
    assert verification.economie_constatee_usd == pytest.approx(verification.economie_attendue_usd)
    assert verification.ecart_pourcentage == pytest.approx(0.0)


def test_r12_economie_coherente_avec_la_facture_cur() -> None:
    """R12 : l'économie constatée se retrouve sur les dollars facturés (EF-VER-02)."""
    socle = charger_socle_demo()
    table = TableTarifs(socle.tarifs)
    avant, apres, volume = _periodes(socle, table)

    cout_avant = cout_par_requete([avant], socle.tenant_a.id)
    cout_apres = cout_par_requete([apres], socle.tenant_a.id)
    ratio = cout_apres / cout_avant

    # Facture CUR 2.0 du jour, par étiquette d'application (EF-ATT-08).
    lignes = generer_lignes_cur(
        _records_classement(),
        table,
        GRAINE_DEFAUT,
        {socle.tenant_a.id: socle.tenant_a.compte_aws},
    )
    dollars_avant = {
        APP_CLASSEMENT: round(
            sum(
                ligne.line_item_unblended_cost
                for ligne in lignes
                if ligne.tag_application == APP_CLASSEMENT
            ),
            4,
        )
    }
    # Après bascule, la même facture est servie par le modèle cible, à volume constant.
    dollars_apres = {APP_CLASSEMENT: round(dollars_avant[APP_CLASSEMENT] * ratio, 4)}
    assert dollars_avant[APP_CLASSEMENT] > 0.0

    economie_attendue = round((cout_avant - cout_apres) * volume, 4)
    confirmation = confirmer_par_etiquette(
        tenant_id=socle.tenant_a.id,
        application_id=APP_CLASSEMENT,
        dollars_avant=dollars_avant,
        dollars_apres=dollars_apres,
        economie_attendue_usd=economie_attendue,
    )

    assert confirmation.conforme is True
    assert abs(confirmation.ecart_pourcentage) < 5.0
    assert confirmation.etiquettes == (APP_CLASSEMENT,)
    assert confirmation.economie_facturee_usd > 0.0


def test_r12_api_expose_les_economies_du_client(
    client_api: TestClient, store_recette: MemoryStore
) -> None:
    """R12 (API) : `GET /v1/tenants/{id}/savings` publie attendu et constaté (EF-VER-01)."""
    socle = charger_socle_demo()
    peupler_store(store_recette, jours=1, date_fin=DATE_FIN_DEFAUT)

    reponse = client_api.get(
        f"/v1/tenants/{socle.tenant_a.id}/savings", headers=entetes(socle.tenant_a.id)
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    verifications = store_recette.lister_verifications(socle.tenant_a.id)

    assert corps["verifications"] == [v.model_dump(mode="json") for v in verifications]
    assert corps["economie_attendue_usd"] == pytest.approx(
        round(sum(v.economie_attendue_usd for v in verifications), 2)
    )
    assert corps["economie_constatee_usd"] == pytest.approx(
        round(sum(v.economie_constatee_usd for v in verifications), 2)
    )
    # La bascule a bien réduit le coût par requête, à volume constant.
    for verification in verifications:
        assert verification.cout_par_requete_apres < verification.cout_par_requete_avant
        assert verification.volume_periode > 0
