"""Recette R1 et R2 — mise en service et journée de trafic de démonstration.

Scénarios du §22 :

- **R1** « Mise en service d'un client » : le parcours d'onboarding (création du
  client et de l'application via l'API du plan de contrôle, table de tarifs
  datée, lecture des journaux, agrégats) produit un **premier coût par
  application** (§8.1, EF-ATT-02..05).
- **R2** « Journée de trafic de démonstration » : sur le trafic synthétique du
  générateur `demo/`, **100 % de la dépense est attribuée** et l'**écart avec la
  facture** CUR 2.0 est calculé et affiché (§8.2, EF-ATT-05..07).

Les deux tests d'interopérabilité M12 ↔ M1 en fin de fichier vérifient que le
parseur d'attribution (M1) lit **brutes** les sorties du générateur de
démonstration (M12) : les journaux d'invocation portent le champ `modelId` des
journaux Bedrock réels (écart É1 corrigé) et les lignes CUR 2.0 portent les
vraies colonnes d'étiquettes utilisateur `resource_tags_user_*` (écart É2
corrigé) — ils ne sont plus marqués `xfail`.

Aucun appel réseau : `MemoryStore`, fichiers locaux, générateur déterministe.
"""

from __future__ import annotations

import json
from datetime import UTC, date
from pathlib import Path

import pytest
from demo.cur import LigneCur as LigneCurDemo
from demo.cur import facture_par_jour as facture_demo_par_jour
from demo.cur import generer_lignes_cur
from demo.profils import DATE_FIN_DEFAUT, GRAINE_DEFAUT
from demo.seed import construire_agregats, peupler_store, resume
from demo.socle import SocleDemo, charger_socle_demo
from demo.traffic import JournalInvocation, generer_trafic, lignes_cloudwatch
from fastapi.testclient import TestClient
from tests.conftest import GRAND_MODELE, REGION
from tests.recette.conftest import ecrire_journal_ndjson, entetes

from jauge.api.auth import RoleUtilisateur
from jauge.attribution.aggregate import AgregateurQuotidien, ResultatAgregation
from jauge.attribution.logs import RapportLecture, iterer_enregistrements, lire_journaux
from jauge.attribution.reconcile import StatistiquesCur, iterer_lignes_cur, rapprocher_periode
from jauge.attribution.reconcile import facture_par_jour as facture_cur_par_jour
from jauge.engine.attribution import rapprocher_facture
from jauge.engine.calculator import TableTarifs
from jauge.models.cost import RapprochementJour
from jauge.models.tenant import Application, Tenant
from jauge.pricing_data import table_demo
from jauge.store.memory import MemoryStore

ADMIN = RoleUtilisateur.ADMINISTRATEUR_PLATEFORME
JOUR_R1 = date(2026, 11, 10)


def _corps_tenant_r1() -> dict[str, object]:
    """Corps de création du client R1 (§8.1 : compte, rôle, identifiant externe)."""
    return {
        "nom": "Client Recette R1",
        "compte_aws": "111222333444",
        "role_arn": "arn:aws:iam::111222333444:role/jauge-dataplane",
        "external_id": "ext-recette-r1-0123456789abcdef",
        "region_principale": REGION,
        "plafond_campagne_usd": 50.0,
        "modeles_autorises": [GRAND_MODELE],
    }


def _journal_premier_jour(app_id: str) -> list[dict[str, object]]:
    """Trois invocations journalisées du premier jour, au format Bedrock (§3.2)."""
    metriques = json.dumps(
        {
            "inputTokenCount": 100,
            "outputTokenCount": 40,
            "cacheReadInputTokenCount": 0,
            "cacheWriteInputTokenCount": 0,
            "invocationLatency": 300,
            "firstByteLatency": 120,
            "serviceTier": "Standard",
        }
    )
    lignes = []
    for index in range(3):
        lignes.append(
            {
                "requestId": f"req-r1-{index:04d}",
                "eventTime": f"{JOUR_R1.isoformat()}T{9 + index:02d}:15:00Z",
                "modelId": GRAND_MODELE,
                "inputTokenCount": 100,
                "outputTokenCount": 40,
                "totalTokenCount": 140,
                "amazon-bedrock-invocationMetrics": metriques,
                "requestMetadata": json.dumps(
                    {"application": app_id, "team": "support", "environment": "production"}
                ),
                "errorCode": None,
                "errorMessage": None,
            }
        )
    return lignes


def _creer_client_et_application(client_api: TestClient) -> tuple[str, str]:
    """Étapes 1 et 2 du parcours d'onboarding : client puis application (§8.1)."""
    reponse = client_api.post(
        "/v1/tenants", json=_corps_tenant_r1(), headers=entetes("t-msp", ADMIN)
    )
    assert reponse.status_code == 201, reponse.text
    tenant_id = reponse.json()["id"]

    reponse = client_api.post(
        f"/v1/tenants/{tenant_id}/applications",
        json={
            "nom": "Classement de tickets",
            "equipe": "support",
            "environnement": "production",
            "modele_actuel": GRAND_MODELE,
            "seuil_qualite": 0.95,
        },
        headers=entetes(tenant_id, ADMIN),
    )
    assert reponse.status_code == 201, reponse.text
    return tenant_id, reponse.json()["id"]


def _aggreger_premier_jour(
    store_recette: MemoryStore, chemin: Path, tenant_id: str, app_id: str
) -> ResultatAgregation:
    """Lecture des journaux, calcul des coûts et agrégation par application (§8.2)."""
    tarifs = table_demo()
    tenant = store_recette.obtenir_tenant(tenant_id)
    application = store_recette.obtenir_application(tenant_id, app_id)
    assert isinstance(tenant, Tenant) and isinstance(application, Application)
    rapport = RapportLecture()
    agregateur = AgregateurQuotidien(tenant, tarifs, applications=[application])
    resultat = agregateur.agreger(lire_journaux(chemin, rapport))
    assert rapport.exploitables == 3
    assert not rapport.rejetes
    return resultat


def test_r1_mise_en_service_produit_un_premier_cout(
    client_api: TestClient,
    store_recette: MemoryStore,
    tmp_path,
) -> None:
    """R1 : onboarding complet → premier coût par application visible (§8.1).

    Parcours : création du client puis de l'application via l'API du plan de
    contrôle, table de tarifs datée chargée depuis `data/pricing/` (EF-ATT-04),
    lecture des journaux du premier jour, agrégation, enregistrement, puis
    lecture du coût via `GET /v1/applications/{id}/costs`.
    """
    # 1-2. Le MSP crée le client, puis une application avec son seuil de qualité.
    tenant_id, app_id = _creer_client_et_application(client_api)

    # 3. Les journaux d'invocation arrivent (EF-ATT-02) : premier jour, 3 appels.
    chemin = ecrire_journal_ndjson(tmp_path / "journaux.jsonl", _journal_premier_jour(app_id))

    # 4. Table de tarifs datée (EF-ATT-04) + agrégation par application (§8.2).
    resultat = _aggreger_premier_jour(store_recette, chemin, tenant_id, app_id)

    assert len(resultat.agregats) == 1
    assert resultat.agregats[0].application_id == app_id
    assert resultat.cout_recalcule_usd > 0.0
    store_recette.enregistrer_agregats(resultat.agregats)

    # 5. Le premier coût par application est lisible sur l'API du plan de contrôle.
    reponse = client_api.get(
        f"/v1/applications/{app_id}/costs",
        params={"debut": JOUR_R1.isoformat(), "fin": JOUR_R1.isoformat()},
        headers=entetes(tenant_id),
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["requetes_total"] == 3
    assert corps["cout_total_usd"] == pytest.approx(resultat.cout_recalcule_usd, abs=1e-4)
    assert corps["cout_total_usd"] > 0.0
    assert GRAND_MODELE in corps["par_modele"]


def _enregistrer_referentiel(store: MemoryStore, socle: SocleDemo) -> None:
    """Enregistre les deux clients et les quatre applications de la démo."""
    store.enregistrer_tenant(socle.tenant_a)
    store.enregistrer_tenant(socle.tenant_b)
    for application in socle.applications.values():
        store.enregistrer_application(application)


def _comptes_demo(socle: SocleDemo) -> dict[str, str]:
    """Compte AWS fictif de chaque client (format attendu par `demo.cur`)."""
    return {
        socle.tenant_a.id: socle.tenant_a.compte_aws,
        socle.tenant_b.id: socle.tenant_b.compte_aws,
    }


def test_r2_journee_demo_100_pourcent_attribuee_et_ecart_affiche(
    store_recette: MemoryStore, table_socle: TableTarifs
) -> None:
    """R2 : une journée de trafic de démonstration, entièrement attribuée, rapprochée.

    Le générateur `demo/traffic.py` produit la journée ; la chaîne officielle de
    la démo (`demo.seed.construire_agregats` → moteur de coût du socle →
    `demo.cur` → `jauge.engine.attribution.rapprocher_facture`) calcule les
    agrégats et l'écart avec la facture CUR 2.0.
    """
    jour = DATE_FIN_DEFAUT
    records = generer_trafic(graine=GRAINE_DEFAUT, jours=1, date_fin=jour)
    assert records, "le générateur de démonstration doit produire du trafic"

    socle = charger_socle_demo()
    _enregistrer_referentiel(store_recette, socle)

    # 1. Agrégats : chaque requête de la journée est attribuée à une application.
    agregats = construire_agregats(records, socle, table_socle)
    store_recette.enregistrer_agregats(agregats)
    requetes_agregees = sum(a.nombre_requetes for a in agregats)
    assert requetes_agregees == len(records), "100 % des requêtes doivent être attribuées"
    assert all(a.application_id in socle.applications for a in agregats)

    # 2. Facture CUR 2.0 de la journée et rapprochement par client (EF-ATT-06).
    lignes_cur = generer_lignes_cur(records, table_socle, GRAINE_DEFAUT, _comptes_demo(socle))
    for tenant in (socle.tenant_a, socle.tenant_b):
        facture = facture_demo_par_jour(lignes_cur, tenant.compte_aws)
        assert facture.get(jour, 0.0) > 0.0, f"pas de facture pour {tenant.id}"
        recalcul = sum(
            a.cout_recalcule_usd for a in agregats if a.tenant_id == tenant.id and a.jour == jour
        )
        rapprochement = rapprocher_facture(
            tenant_id=tenant.id, jour=jour, cout_recalcule=recalcul, cout_facture=facture[jour]
        )
        store_recette.enregistrer_rapprochement(rapprochement)

        # L'écart avec la facture est calculé et sous la cible de 3 % (§4).
        assert rapprochement.ecart_pourcentage < 3.0
        assert rapprochement.conforme is True
        assert rapprochement.part_non_attribuee_pourcentage == 0.0

    # 3. L'écart est bien « affiché » : le résumé console de la démo le publie.
    affichage = resume(store_recette, jours=1)
    assert "rapprochement : écart moyen" in affichage
    assert "jours conformes" in affichage


def test_r2_store_amorce_par_la_demo_expose_une_couverture_100(
    store_recette: MemoryStore,
) -> None:
    """R2 (complément) : amorçage officiel `demo.seed` → couverture 100 %, écart < 3 %.

    `peupler_store` est le parcours d'amorçage du portail de démonstration
    (EF-POR-02) ; la synthèse de coût du store doit afficher une couverture
    d'attribution de 100 % et un écart de rapprochement moyen sous la cible.
    """
    peupler_store(store_recette, jours=1, date_fin=DATE_FIN_DEFAUT)
    synthese = store_recette.synthese_cout("t-demo-a", DATE_FIN_DEFAUT, DATE_FIN_DEFAUT)
    assert synthese.cout_total_usd > 0.0
    assert synthese.couverture_attribution_pourcentage == pytest.approx(100.0)
    assert abs(synthese.ecart_rapprochement_moyen_pourcentage) < 3.0


def test_r2_le_parseur_m1_lit_les_journaux_demo_bruts() -> None:
    """R2 (interop M12 ↔ M1) : le parseur M1 lit les lignes CloudWatch brutes.

    Les lignes de la démo portent les champs des journaux d'invocation Bedrock
    réels — `modelId`, `eventTime`, compteurs de jetons, `serviceTier` et
    étiquettes d'attribution dans `requestMetadata` : M1 les ingère toutes,
    sans aucun rejet « model_id absent » ni adaptation (écart É1 corrigé).
    """
    records = generer_trafic(graine=GRAINE_DEFAUT, jours=1, date_fin=DATE_FIN_DEFAUT)
    assert records, "le générateur de démonstration doit produire du trafic"
    rapport = RapportLecture()
    enregistrements = list(iterer_enregistrements(lignes_cloudwatch(records), rapport))
    assert len(enregistrements) > 0
    assert rapport.exploitables == len(records)
    assert "model_id absent" not in rapport.motifs_rejet
    assert not rapport.rejetes, f"rejets M1 : {rapport.motifs_rejet}"

    # Les champs émis par la démo sont bien reconnus par `CHAMPS_ACCEPTES`
    # (comparaison en ordre : la lecture M1 préserve l'ordre du flux source).
    assert len(enregistrements) == len(records)
    for record, lu in zip(records, enregistrements, strict=True):
        assert lu.request_id == record.request_id
        assert lu.model_id == record.modele_id
        assert lu.niveau_service == record.niveau
        assert lu.jetons_entree == record.jetons_entree
        assert lu.jetons_sortie == record.jetons_sortie
        assert lu.jetons_cache_lecture == record.jetons_cache_lecture
        assert lu.jetons_cache_ecriture == record.jetons_cache_ecriture
        assert lu.horodatage == record.event_time.astimezone(UTC).replace(microsecond=0)
        assert lu.etiquettes.application == record.application_id
        assert lu.etiquettes.est_attribuee


def _rapprochements_m1(
    tenant: Tenant,
    records: list[JournalInvocation],
    lignes_cur: list[LigneCurDemo],
    applications: list[Application],
    table: TableTarifs,
) -> list[RapprochementJour]:
    """Rapprochement M1 d'un client, branché directement sur les sorties brutes.

    Toute la chaîne est celle des modules : `demo.traffic.lignes_cloudwatch`
    (M12) → `logs.iterer_enregistrements` → `AgregateurQuotidien` →
    `reconcile.iterer_lignes_cur` → `reconcile.facture_par_jour` →
    `reconcile.rapprocher_periode` (M1), sans aucune adaptation de format.
    """
    du_client = [r for r in records if r.tenant_id == tenant.id]
    rapport = RapportLecture()
    agregateur = AgregateurQuotidien(tenant, table, applications=applications)
    resultat = agregateur.agreger(iterer_enregistrements(lignes_cloudwatch(du_client), rapport))
    assert rapport.exploitables == len(du_client)
    assert not rapport.rejetes, f"rejets M1 : {rapport.motifs_rejet}"

    facture_brute = (
        ligne.vers_dictionnaire()
        for ligne in lignes_cur
        if ligne.payer_account_id == tenant.compte_aws
    )
    factures = facture_cur_par_jour(iterer_lignes_cur(facture_brute))
    return rapprocher_periode([resultat], factures.values(), tenant.id)


def test_r2_le_parseur_m1_attribue_les_lignes_cur_demo(
    table_socle: TableTarifs,
) -> None:
    """R2 (interop M12 ↔ M1) : les lignes CUR de la démo sont attribuées par M1.

    La démo émet les vraies colonnes CUR 2.0 des étiquettes utilisateur
    (`resource_tags/user:<clé>`, normalisées `resource_tags_user_<clé>`) : le
    parseur CUR de M1 (`CHAMPS_CUR`) les lit sans adaptation, la dépense est
    attribuée à 100 % et le rapprochement quotidien avec les journaux bruts,
    agrégés par M1, affiche un écart sous la cible de 3 % (écart É2 corrigé).
    """
    socle = charger_socle_demo()
    records = generer_trafic(graine=GRAINE_DEFAUT, jours=1, date_fin=DATE_FIN_DEFAUT)
    lignes = generer_lignes_cur(records, table_socle, GRAINE_DEFAUT, _comptes_demo(socle))
    assert lignes, "la démo doit produire des lignes CUR"

    # 1. Le parseur CUR de M1 retient toutes les lignes et les attribue à 100 %.
    stats = StatistiquesCur()
    brutes = [ligne.vers_dictionnaire() for ligne in lignes]
    parsees = list(iterer_lignes_cur(brutes, stats=stats))
    assert len(parsees) > 0
    assert stats.retenues == stats.lues == len(lignes)
    assert all(ligne.attribuee for ligne in parsees), "ligne CUR vue non attribuée"
    factures = facture_cur_par_jour(parsees)
    assert factures, "aucune facture consolidée"
    for facture in factures.values():
        assert facture.cout_non_attribue_usd == 0.0
        assert facture.part_non_attribuee_pourcentage == 0.0

    # 2. Chaîne complète M12 → M1 : l'écart de rapprochement est affiché et < 3 %.
    for tenant in (socle.tenant_a, socle.tenant_b):
        rapprochements = _rapprochements_m1(
            tenant, records, lignes, socle.par_tenant(tenant.id), table_socle
        )
        assert rapprochements, f"aucun rapprochement pour {tenant.id}"
        for rapprochement in rapprochements:
            assert rapprochement.conforme, rapprochement
            assert rapprochement.ecart_pourcentage < 3.0
            assert rapprochement.part_non_attribuee_pourcentage == 0.0
