"""Tests du rapport mensuel par client (EF-RAP-01, EF-RAP-03, §4, §7.2, §8.5).

Les valeurs attendues sont calculées à la main sur le jeu de données de
`conftest.py` : 55 USD et 350 requêtes en octobre 2026, 40 USD en septembre,
écart de facture de 1.79 %, économies attendues 12 USD contre 9.5 USD
constatées. Si un test échoue, c'est le rapport qui a bougé, pas le socle.
"""

from __future__ import annotations

from datetime import date

import pytest
from tests.conftest import GRAND_MODELE, PETIT_MODELE

from jauge.common.errors import IntrouvableError, ValidationError
from jauge.models.lever import StatutLevier
from jauge.reports.builder import construire_rapport, mois_precedent, periode_mensuelle
from jauge.store.memory import MemoryStore

MOIS = (2026, 10)


def test_periode_et_mois_precedent() -> None:
    """Les bornes du mois et la bascule d'année sont exactes."""
    assert periode_mensuelle(2026, 2) == (date(2026, 2, 1), date(2026, 2, 28))
    assert periode_mensuelle(2026, 12)[1].isoformat() == "2026-12-31"
    assert mois_precedent(2026, 1) == (2025, 12)
    assert mois_precedent(2026, 10) == (2026, 9)


def test_periode_refuse_un_mois_invalide() -> None:
    """Un mois hors 1..12 est refusé avec l'erreur métier du socle."""
    with pytest.raises(ValidationError):
        periode_mensuelle(2026, 13)


def test_client_inconnu_leve_introuvable(store_peuple: MemoryStore) -> None:
    """Un client absent du store ne produit pas de rapport vide silencieux."""
    with pytest.raises(IntrouvableError):
        construire_rapport(store_peuple, "t-inconnu", *MOIS)


def test_totaux_du_mois(store_peuple: MemoryStore) -> None:
    """Dépense et volume du mois sont ceux des agrégats du mois, pas d'un autre."""
    rapport = construire_rapport(store_peuple, "t-demo-a", *MOIS)
    assert rapport.cout_total_usd == pytest.approx(55.0)
    assert rapport.nombre_requetes == 350
    assert rapport.periode_debut.isoformat() == "2026-10-01"
    assert rapport.periode_fin.isoformat() == "2026-10-31"
    assert rapport.devise == "USD"
    assert rapport.schema_version == "1.0"


def test_depense_par_application_triee_et_partagee(store_peuple: MemoryStore) -> None:
    """Dépense par application, triée décroissante, avec parts cohérentes."""
    rapport = construire_rapport(store_peuple, "t-demo-a", *MOIS)
    assert [ligne.cle for ligne in rapport.par_application] == [
        "ticket-classifier",
        "support-assistant",
    ]
    assert rapport.par_application[0].cout_usd == pytest.approx(35.0)
    assert rapport.par_application[0].libelle == "Classement de tickets"
    assert rapport.par_application[0].part_pourcentage == pytest.approx(63.64)
    assert sum(ligne.part_pourcentage for ligne in rapport.par_application) == pytest.approx(
        100.0, abs=0.05
    )


def test_depense_par_equipe_et_par_modele(store_peuple: MemoryStore) -> None:
    """Les deux autres axes d'attribution sont présents (EF-RAP-01)."""
    rapport = construire_rapport(store_peuple, "t-demo-a", *MOIS)
    assert [ligne.cle for ligne in rapport.par_equipe] == ["support"]
    assert rapport.par_equipe[0].cout_usd == pytest.approx(55.0)
    # Deux modèles distincts dans les agrégats, tous deux présents et triés.
    assert [ligne.cle for ligne in rapport.par_modele] == [GRAND_MODELE, PETIT_MODELE]
    assert rapport.par_modele[0].cout_usd == pytest.approx(50.0)
    assert rapport.par_modele[0].nombre_requetes == 150
    assert rapport.par_modele[1].cout_usd == pytest.approx(5.0)


def test_tendance_par_rapport_au_mois_precedent(store_peuple: MemoryStore) -> None:
    """La tendance compare octobre à septembre : +15 USD, +37.5 %, hausse."""
    rapport = construire_rapport(store_peuple, "t-demo-a", *MOIS)
    tendance = rapport.tendance
    assert tendance.cout_mois_precedent_usd == pytest.approx(40.0)
    assert tendance.variation_usd == pytest.approx(15.0)
    assert tendance.variation_pourcentage == pytest.approx(37.5)
    assert tendance.sens == "hausse"


def test_ecart_de_rapprochement_du_mois(store_peuple: MemoryStore) -> None:
    """L'écart mensuel est formé par le moteur du socle : 55 vs 56 USD."""
    ecart = construire_rapport(store_peuple, "t-demo-a", *MOIS).ecart_facture
    assert ecart.disponible is True
    assert ecart.cout_recalcule_usd == pytest.approx(55.0)
    assert ecart.cout_facture_usd == pytest.approx(56.0)
    assert ecart.ecart_usd == pytest.approx(-1.0)
    assert ecart.ecart_pourcentage == pytest.approx(1.79)
    assert ecart.conforme is True
    assert ecart.jours_rapproches == 2
    assert ecart.causes_non_attribuees == ["unlabelled calls"]


def test_ecart_indisponible_hors_rapprochement(store_peuple: MemoryStore) -> None:
    """Un mois sans rapprochement affiche l'absence, sans inventer un écart nul."""
    ecart = construire_rapport(store_peuple, "t-demo-a", 2026, 8).ecart_facture
    assert ecart.disponible is False
    assert ecart.jours_rapproches == 0
    assert ecart.cout_facture_usd == 0.0


def test_leviers_excluent_les_rejetes(store_peuple: MemoryStore) -> None:
    """Les leviers rejetés ne figurent pas au rapport ; le reste est trié."""
    leviers = construire_rapport(store_peuple, "t-demo-a", *MOIS).leviers
    assert [levier.id for levier in leviers] == ["lv-prouve", "lv-applique"]
    assert leviers[0].economie_mensuelle_estimee_usd == pytest.approx(12.0)
    assert leviers[0].application_nom == "Classement de tickets"
    assert leviers[1].applique is True
    assert leviers[1].statut == str(StatutLevier.APPLIQUE)


def test_economies_attendues_et_constatees_distinctes(store_peuple: MemoryStore) -> None:
    """Attendues et constatées restent deux chiffres séparés (§4, §7.2)."""
    economies = construire_rapport(store_peuple, "t-demo-a", *MOIS).economies
    assert economies.attendues_usd == pytest.approx(12.0)
    assert economies.constatees_usd == pytest.approx(9.5)
    assert economies.ecart_usd == pytest.approx(2.5)
    assert economies.taux_realisation_pourcentage == pytest.approx(79.17)
    assert economies.restantes_usd == pytest.approx(12.0)
    assert economies.nombre_verifications == 1


def test_recommandations_en_attente_seulement(store_peuple: MemoryStore) -> None:
    """Seules les recommandations en attente d'approbation sont listées (§8.5)."""
    en_attente = construire_rapport(store_peuple, "t-demo-a", *MOIS).recommandations_en_attente
    assert [reco.id for reco in en_attente] == ["r-en-attente"]
    assert en_attente[0].economie_mensuelle_attendue_usd == pytest.approx(8.0)
    assert en_attente[0].application_nom == "Assistant de support"


def test_etat_des_bascules(store_peuple: MemoryStore) -> None:
    """Palier courant, qualité en ligne et seuil d'alerte sont repris tels quels."""
    bascules = construire_rapport(store_peuple, "t-demo-a", *MOIS).bascules
    assert len(bascules) == 1
    assert bascules[0].palier_actuel_pourcentage == 50
    assert bascules[0].actif is True
    assert bascules[0].seuil_alerte == pytest.approx(0.90)


def test_appendice_methodologique(store_peuple: MemoryStore, tarifs_table: list) -> None:
    """Seuil, échantillon, intervalle de confiance et date des tarifs (EF-RAP-03)."""
    methodologie = construire_rapport(
        store_peuple, "t-demo-a", *MOIS, tarifs=tarifs_table
    ).methodologie
    assert methodologie.seuil_qualite == pytest.approx(0.95)
    assert methodologie.seuil_intervalle_bas == pytest.approx(0.90)
    assert methodologie.taille_echantillon == 200
    assert methodologie.intervalle_confiance_pourcentage == pytest.approx(95.0)
    assert methodologie.date_table_tarifs.isoformat() == "2026-01-01"
    assert methodologie.region == "eu-west-1"
    assert methodologie.sources


def test_identifiant_stable_et_reproductible(store_peuple: MemoryStore) -> None:
    """Deux constructions du même rapport donnent le même identifiant."""
    premier = construire_rapport(store_peuple, "t-demo-a", *MOIS)
    second = construire_rapport(store_peuple, "t-demo-a", *MOIS)
    assert premier.id == second.id
    assert premier.id.startswith("rp-")
    autre = construire_rapport(store_peuple, "t-demo-a", 2026, 9)
    assert autre.id != premier.id


def test_cloisonnement_client_b(store_peuple: MemoryStore, tenant_b) -> None:
    """Le client B n'obtient aucun chiffre du client A (ENF-SEC-02)."""
    store_peuple.enregistrer_tenant(tenant_b)
    rapport = construire_rapport(store_peuple, tenant_b.id, *MOIS)
    assert rapport.cout_total_usd == 0.0
    assert rapport.nombre_requetes == 0
    assert rapport.par_application == []
    assert rapport.leviers == []
    assert rapport.recommandations_en_attente == []
    assert rapport.bascules == []
    assert rapport.economies.constatees_usd == 0.0
