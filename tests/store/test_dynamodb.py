"""Tests du store DynamoDB : contrat, cloisonnement, parité, idempotence.

Aucun appel AWS : le store parle au port `TableEtat`, doublé ici par
`tests/store/fake_dynamo.py` (qui imite jusqu'à la projection KEYS_ONLY de
l'index `par-type`). L'adaptateur boto3 est testé séparément, sur la traduction
d'erreur uniquement.
"""

from __future__ import annotations

from datetime import date

import pytest
from tests.conftest import GRAND_MODELE, PETIT_MODELE
from tests.store.fake_dynamo import FakeTableEtat

from jauge.common.errors import ServiceIndisponibleError
from jauge.models.campaign import Campagne
from jauge.models.cost import AgregatQuotidien, RapprochementJour
from jauge.models.lever import Levier, StatutLevier, TypeLevier
from jauge.models.recommendation import (
    BasculeProgressive,
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
    VerificationEconomie,
)
from jauge.models.tenant import Application, Tenant
from jauge.store.dynamodb import DynamoDbStore
from jauge.store.memory import MemoryStore
from jauge.store.table import TableBoto3

JOUR = date(2026, 11, 1)
JOUR_2 = date(2026, 11, 2)
APP_A = "ticket-classifier"
APP_B = "kb-search"


@pytest.fixture
def table() -> FakeTableEtat:
    """Table d'état en mémoire."""
    return FakeTableEtat()


@pytest.fixture
def store(table: FakeTableEtat) -> DynamoDbStore:
    """Store DynamoDB branché sur la table en mémoire."""
    return DynamoDbStore(table)


def _agregat(tenant_id: str, app_id: str, jour: date, cout: float) -> AgregatQuotidien:
    """Agrégat quotidien minimal pour les tests."""
    return AgregatQuotidien(
        tenant_id=tenant_id,
        application_id=app_id,
        equipe="support",
        environnement="production",
        modele_id=GRAND_MODELE,
        jour=jour,
        nombre_requetes=100,
        jetons_entree_total=100_000,
        jetons_sortie_total=5_000,
        cout_recalcule_usd=cout,
    )


def _recommandation(reco_id: str, tenant_id: str, app_id: str) -> Recommandation:
    """Recommandation au format de l'Annexe B."""
    return Recommandation(
        id=reco_id,
        tenantId=tenant_id,
        applicationId=app_id,
        currentModel=GRAND_MODELE,
        targetModel=PETIT_MODELE,
        campaignId="c-1",
        sampleSize=200,
        quality=MetriquesQualite(
            equivalenceRate=0.975,
            confidenceIntervalLow=0.943,
            selfAgreementOfCurrentModel=0.985,
            deterministicFailures=0,
        ),
        cost=MetriquesCout(
            costPerRequestBefore=0.0041,
            costPerRequestAfter=0.0009,
            monthlyRequests=310_000,
            expectedMonthlySaving=992.0,
        ),
        latency=MetriquesLatence(p50Before=820, p50After=410, p95Before=2100, p95After=900),
    )


# --- contrat : écriture / lecture -------------------------------------------


def test_aller_retour_client_et_application(
    store: DynamoDbStore, tenant: Tenant, app_classement: Application
) -> None:
    """Un client et son application survivent à l'aller-retour DynamoDB."""
    store.enregistrer_tenant(tenant)
    store.enregistrer_application(app_classement)

    assert store.obtenir_tenant(tenant.id) == tenant
    assert store.obtenir_application(tenant.id, app_classement.id) == app_classement
    assert [t.id for t in store.lister_tenants()] == [tenant.id]
    assert [a.id for a in store.lister_applications(tenant.id)] == [app_classement.id]


def test_schema_de_cles_conforme_au_cdk(
    store: DynamoDbStore, table: FakeTableEtat, tenant: Tenant, app_classement: Application
) -> None:
    """Les clés suivent le schéma du CDK : `TENANT#<id>` / `<TYPE>#<ident>`."""
    store.enregistrer_tenant(tenant)
    store.enregistrer_application(app_classement)

    assert f"TENANT#{tenant.id}" in table.partitions()
    assert "REGISTRY#TENANTS" in table.partitions()
    item = table.lire(f"TENANT#{tenant.id}", f"APP#{app_classement.id}")
    assert item is not None
    assert item["type"] == "APP"
    assert item["GSI1PK"] == "TYPE#APP"
    assert item["GSI1SK"].startswith(f"{tenant.id}#")
    assert "doc" in item


def test_aller_retour_agregats_et_rapprochement(store: DynamoDbStore, tenant: Tenant) -> None:
    """Agrégats et rapprochements sont relus, triés et filtrables."""
    store.enregistrer_agregats(
        [_agregat(tenant.id, APP_A, JOUR, 10.0), _agregat(tenant.id, APP_A, JOUR_2, 12.0)]
    )
    store.enregistrer_rapprochement(
        RapprochementJour(
            tenant_id=tenant.id,
            jour=JOUR,
            cout_recalcule_usd=10.0,
            cout_facture_usd=10.2,
            ecart_usd=-0.2,
            ecart_pourcentage=1.96,
            part_non_attribuee_pourcentage=0.0,
        )
    )

    tous = store.lister_agregats(tenant.id)
    assert len(tous) == 2
    assert [a.jour for a in tous] == [JOUR, JOUR_2]  # tri chronologique par clé de tri
    assert len(store.lister_agregats(tenant.id, debut=JOUR_2)) == 1
    assert len(store.lister_agregats(tenant.id, fin=JOUR)) == 1
    assert len(store.lister_rapprochements(tenant.id)) == 1


def test_ecriture_idempotente_des_agregats(store: DynamoDbStore, table: FakeTableEtat) -> None:
    """Rejouer la même journée remplace l'agrégat au lieu de le dupliquer."""
    tenant_id = "t-demo-a"
    store.enregistrer_agregats([_agregat(tenant_id, APP_A, JOUR, 10.0)])
    store.enregistrer_agregats([_agregat(tenant_id, APP_A, JOUR, 11.0)])

    agregats = store.lister_agregats(tenant_id)
    assert len(agregats) == 1
    assert agregats[0].cout_recalcule_usd == 11.0
    assert table.nb_items == 1


# --- cloisonnement (ENF-SEC-02, recette R11) ---------------------------------


def test_cloisonnement_structurel_des_applications(
    store: DynamoDbStore, table: FakeTableEtat, tenant: Tenant, tenant_b: Tenant
) -> None:
    """Le client B ne voit pas l'application du client A — et ne peut pas la lire."""
    store.enregistrer_tenant(tenant)
    store.enregistrer_tenant(tenant_b)
    store.enregistrer_application(
        Application(
            id=APP_A, tenant_id=tenant.id, nom="Classement", equipe="support", modele_actuel="m"
        )
    )
    store.enregistrer_application(
        Application(
            id=APP_B, tenant_id=tenant_b.id, nom="Recherche", equipe="data", modele_actuel="m"
        )
    )

    assert store.obtenir_application(tenant_b.id, APP_A) is None
    assert [a.id for a in store.lister_applications(tenant_b.id)] == [APP_B]
    # Le cloisonnement est structurel : une partition par client, pas un filtre.
    assert f"TENANT#{tenant.id}" in table.partitions()
    assert f"TENANT#{tenant_b.id}" in table.partitions()


def test_cloisonnement_campagnes_recommandations_bascules(
    store: DynamoDbStore, tenant: Tenant, tenant_b: Tenant
) -> None:
    """Campagnes, recommandations et bascules d'un client sont invisibles à l'autre."""
    store.enregistrer_campagne(
        Campagne(
            id="c-1",
            tenant_id=tenant.id,
            application_id=APP_A,
            modeles_candidats=[PETIT_MODELE],
            cout_estime_usd=1.5,
        )
    )
    store.enregistrer_recommandation(_recommandation("r-1", tenant.id, APP_A))
    store.enregistrer_bascule(
        BasculeProgressive(
            id="ro-1",
            tenant_id=tenant.id,
            recommandation_id="r-1",
            application_id=APP_A,
            modele_cible=PETIT_MODELE,
            regle_passerelle_id="regle-1",
        )
    )

    assert store.obtenir_campagne(tenant_b.id, "c-1") is None
    assert store.lister_campagnes(tenant_b.id) == []
    assert store.obtenir_recommandation(tenant_b.id, "r-1") is None
    assert store.lister_recommandations(tenant_b.id) == []
    assert store.obtenir_bascule(tenant_b.id, "ro-1") is None
    assert store.lister_bascules(tenant_b.id) == []


def test_agregats_et_leviers_ne_fuient_pas(
    store: DynamoDbStore, tenant: Tenant, tenant_b: Tenant
) -> None:
    """Agrégats, rapprochements et leviers restent dans leur partition client."""
    store.enregistrer_agregats([_agregat(tenant.id, APP_A, JOUR, 10.0)])
    store.enregistrer_rapprochement(
        RapprochementJour(
            tenant_id=tenant.id,
            jour=JOUR,
            cout_recalcule_usd=10.0,
            cout_facture_usd=10.0,
            ecart_usd=0.0,
            ecart_pourcentage=0.0,
            part_non_attribuee_pourcentage=0.0,
        )
    )
    store.enregistrer_levier(
        Levier(
            id="lv-1",
            tenant_id=tenant.id,
            application_id=APP_A,
            type_levier=TypeLevier.MODELE_SURDIMENSIONNE,
            action_proposee="Rejeu sur des modèles plus petits",
            preuve_exigee="Campagne de rejeu",
            economie_mensuelle_estimee_usd=992.0,
        )
    )

    assert store.lister_agregats(tenant_b.id) == []
    assert store.lister_rapprochements(tenant_b.id) == []
    assert store.lister_leviers(tenant_b.id) == []
    assert store.synthese_cout(tenant_b.id, JOUR, JOUR_2).cout_total_usd == 0.0


# --- leviers : mise à jour par identifiant seul -------------------------------


def test_maj_statut_levier_via_index_par_type(store: DynamoDbStore, tenant: Tenant) -> None:
    """`maj_statut_levier` retrouve le levier via l'index, en projection KEYS_ONLY."""
    store.enregistrer_levier(
        Levier(
            id="lv-1",
            tenant_id=tenant.id,
            application_id=APP_A,
            type_levier=TypeLevier.CACHE_PROMPTS_ABSENT,
            action_proposee="Activer le cache de prompts",
            preuve_exigee="Calcul sur les jetons",
            economie_mensuelle_estimee_usd=64.0,
        )
    )
    store.maj_statut_levier("lv-1", StatutLevier.PROUVE.value)

    leviers = store.lister_leviers(tenant.id)
    assert len(leviers) == 1
    assert leviers[0].statut == StatutLevier.PROUVE


def test_maj_statut_levier_inconnu_sans_effet(store: DynamoDbStore) -> None:
    """Un identifiant inconnu ne fait rien (comme le store mémoire)."""
    store.maj_statut_levier("lv-inexistant", StatutLevier.REJETE.value)


def test_levier_filtre_par_application(store: DynamoDbStore, tenant: Tenant) -> None:
    """Le filtre par application est appliqué à la lecture."""
    for index, app in enumerate((APP_A, APP_B)):
        store.enregistrer_levier(
            Levier(
                id=f"lv-{index}",
                tenant_id=tenant.id,
                application_id=app,
                type_levier=TypeLevier.REQUETES_REPETEES,
                action_proposee="Cache applicatif",
                preuve_exigee="Comptage",
                economie_mensuelle_estimee_usd=10.0 * (index + 1),
            )
        )
    assert len(store.lister_leviers(tenant.id)) == 2
    assert [lv.id for lv in store.lister_leviers(tenant.id, app_id=APP_B)] == ["lv-1"]


# --- vérifications d'économie (registre transverse) ----------------------------


def test_verifications_filtrees_par_client(
    store: DynamoDbStore, tenant: Tenant, tenant_b: Tenant
) -> None:
    """Les vérifications sont filtrées par les applications du client demandé."""
    store.enregistrer_application(
        Application(
            id=APP_A, tenant_id=tenant.id, nom="Classement", equipe="support", modele_actuel="m"
        )
    )
    store.enregistrer_application(
        Application(
            id=APP_B, tenant_id=tenant_b.id, nom="Recherche", equipe="data", modele_actuel="m"
        )
    )
    for app, reco in ((APP_A, "r-1"), (APP_B, "r-2")):
        store.enregistrer_verification(
            VerificationEconomie(
                application_id=app,
                recommandation_id=reco,
                cout_par_requete_avant=0.01,
                cout_par_requete_apres=0.004,
                volume_periode=10_000,
                economie_constatee_usd=60.0,
                economie_attendue_usd=60.0,
                ecart_pourcentage=0.0,
            )
        )

    assert [v.recommandation_id for v in store.lister_verifications(tenant.id)] == ["r-1"]
    assert [v.recommandation_id for v in store.lister_verifications(tenant_b.id)] == ["r-2"]


# --- parité avec le store mémoire ---------------------------------------------


def test_parite_avec_le_store_memoire(tenant: Tenant, app_classement: Application) -> None:
    """Le même scénario sur les deux stores donne les mêmes résultats."""
    memoire = MemoryStore()
    dynamo = DynamoDbStore(FakeTableEtat())

    for store in (memoire, dynamo):
        store.enregistrer_tenant(tenant)
        store.enregistrer_application(app_classement)
        store.enregistrer_agregats(
            [
                _agregat(tenant.id, app_classement.id, JOUR, 10.0),
                _agregat(tenant.id, app_classement.id, JOUR_2, 12.0),
            ]
        )
        store.enregistrer_rapprochement(
            RapprochementJour(
                tenant_id=tenant.id,
                jour=JOUR,
                cout_recalcule_usd=10.0,
                cout_facture_usd=10.2,
                ecart_usd=-0.2,
                ecart_pourcentage=1.96,
                part_non_attribuee_pourcentage=0.0,
            )
        )
        store.enregistrer_recommandation(_recommandation("r-1", tenant.id, app_classement.id))

    assert dynamo.lister_applications(tenant.id) == memoire.lister_applications(tenant.id)
    assert dynamo.lister_agregats(tenant.id) == memoire.lister_agregats(tenant.id)
    assert dynamo.lister_rapprochements(tenant.id) == memoire.lister_rapprochements(tenant.id)
    assert dynamo.lister_recommandations(tenant.id) == memoire.lister_recommandations(tenant.id)
    assert dynamo.synthese_cout(tenant.id, JOUR, JOUR_2) == memoire.synthese_cout(
        tenant.id, JOUR, JOUR_2
    )


def test_synthese_cout_couvre_100_pourcent(store: DynamoDbStore, tenant: Tenant) -> None:
    """La couverture d'attribution vaut 100 % quand tout est rattaché."""
    store.enregistrer_application(
        Application(
            id=APP_A, tenant_id=tenant.id, nom="Classement", equipe="support", modele_actuel="m"
        )
    )
    store.enregistrer_agregats([_agregat(tenant.id, APP_A, JOUR, 10.0)])

    synthese_cout = store.synthese_cout(tenant.id, JOUR, JOUR_2)
    assert synthese_cout.cout_total_usd == 10.0
    assert synthese_cout.couverture_attribution_pourcentage == 100.0


# --- adaptateur boto3 : traduction d'erreur ------------------------------------


class _TableQuiEchoue:
    """Ressource boto3 factice dont chaque opération échoue."""

    def __getattr__(self, nom: str):
        """Toute opération lève une erreur de service."""

        def _echouer(**kwargs):
            raise RuntimeError("ResourceNotFoundException")

        return _echouer


def test_erreur_boto3_traduite_en_erreur_metier() -> None:
    """Une panne DynamoDB remonte en `ServiceIndisponibleError`, jamais brute."""
    table = TableBoto3(_TableQuiEchoue())
    with pytest.raises(ServiceIndisponibleError) as erreur:
        table.lire("TENANT#t", "APP#a")
    assert erreur.value.statut_http == 502
    assert erreur.value.details["operation"] == "get_item"

    with pytest.raises(ServiceIndisponibleError):
        table.ecrire({"PK": "TENANT#t", "SK": "APP#a"})
    with pytest.raises(ServiceIndisponibleError):
        table.requeter("TENANT#t")
    with pytest.raises(ServiceIndisponibleError):
        table.requeter_index("TYPE#APP")
    with pytest.raises(ServiceIndisponibleError):
        table.supprimer("TENANT#t", "APP#a")


# --- fabrique : choix du store par l'environnement ------------------------------


def test_fabrique_store_memoire_par_defaut(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans configuration, le store est en mémoire (démo, tests, hors ligne)."""
    from jauge.store import creer_store

    monkeypatch.delenv("JAUGE_STORE", raising=False)
    assert isinstance(creer_store(), MemoryStore)


def test_fabrique_store_dynamodb(monkeypatch: pytest.MonkeyPatch) -> None:
    """`JAUGE_STORE=dynamodb` construit le store DynamoDB (boto3 non appelé)."""
    from jauge.store import creer_store

    monkeypatch.setenv("JAUGE_STORE", "dynamodb")
    monkeypatch.setenv("JAUGE_DYNAMO_TABLE", "jauge-state")
    monkeypatch.setattr(
        TableBoto3, "depuis_environnement", lambda nom, region=None: FakeTableEtat()
    )
    store = creer_store()
    assert isinstance(store, DynamoDbStore)


def test_fabrique_refuse_dynamodb_sans_nom_de_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configuration incomplète ⇒ erreur explicite, jamais un store silencieux."""
    from jauge.store import creer_store

    monkeypatch.setenv("JAUGE_STORE", "dynamodb")
    monkeypatch.delenv("JAUGE_DYNAMO_TABLE", raising=False)
    with pytest.raises(ServiceIndisponibleError) as erreur:
        creer_store()
    assert erreur.value.details["variable"] == "JAUGE_DYNAMO_TABLE"


def test_point_entree_asgi_hors_demo_prend_le_store_dynamodb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Déploiement réel : `JAUGE_MODE_DEMO=0` + `JAUGE_STORE=dynamodb` ⇒ store DynamoDB.

    C'est la configuration que pose le CDK sur la Lambda (`constructs/api.py`) :
    sans elle, l'API démarrerait sur le store en mémoire et perdrait tout état à
    chaque conteneur froid.
    """
    import jauge.api.app as module_app

    monkeypatch.setenv("JAUGE_MODE_DEMO", "0")
    monkeypatch.setenv("JAUGE_STORE", "dynamodb")
    monkeypatch.setenv("JAUGE_STATE_TABLE", "jauge-state")
    monkeypatch.setattr(
        TableBoto3, "depuis_environnement", lambda nom, region=None: FakeTableEtat()
    )

    application = module_app.creer_app_asgi()
    assert isinstance(application.state.store, DynamoDbStore)
    assert application.state.mode_demo is False
