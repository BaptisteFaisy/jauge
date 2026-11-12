"""Tests du backtest contrefactuel (piste n° 5 du top 10, EF-VER-01, §4).

Mini-jeu **vérifié à la main** : trois jours, 10 / 20 / 30 requêtes, chacune de
1 000 jetons d'entrée et 1 000 jetons de sortie.

- modèle réel (`GRAND_MODELE`, Standard) : 0,015 $/1k en entrée et 0,075 $/1k en
  sortie → **0,09 $ par requête** ;
- candidat (`PETIT_MODELE`, Standard) : 0,0008 $/1k en entrée et 0,004 $/1k en
  sortie → **0,0048 $ par requête**.

D'où, à volume constant (60 requêtes) :

- coût réel de la période : 60 × 0,09 = **5,40 $** ;
- coût contrefactuel : 60 × 0,0048 = **0,288 $** ;
- économie : 5,40 − 0,288 = **5,112 $**, soit (5,40 − 0,288) / 5,40 = **94,67 %** ;
- projection mensuelle : 20 requêtes/jour × 30 jours = 600 requêtes,
  (0,09 − 0,0048) × 600 = **51,12 $**.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import date

import pytest
from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE, REGION

from jauge.common.errors import TarifInconnuError, ValidationError
from jauge.engine.calculator import TableTarifs
from jauge.models.cost import AgregatQuotidien
from jauge.models.pricing import ServiceTier, TarifModele
from jauge.replay.backtest import (
    MENTION_PROJECTION,
    ResultatBacktest,
    VolumeBacktest,
    backtester,
    volumes_depuis_agregats,
)

TENANT = "t-demo-a"
APPLICATION = "ticket-classifier"
EQUIPE = "support"
JOURS = (date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3))
REQUETES_PAR_JOUR = (10, 20, 30)
JETONS_PAR_REQUETE = 1000
# Coûts réels par jour, calculés à la main : 10 × 0,09 / 20 × 0,09 / 30 × 0,09.
COUT_REEL_PAR_JOUR = (0.9, 1.8, 2.7)
# Coûts contrefactuels attendus : 10 × 0,0048 / 20 × 0,0048 / 30 × 0,0048.
COUT_CANDIDAT_PAR_JOUR = (0.048, 0.096, 0.144)
ECONOMIE_PAR_JOUR = (0.852, 1.704, 2.556)
MODELE_CHER = "demo-modele-hors-de-prix"


@pytest.fixture
def table(tarifs: list[TarifModele]) -> TableTarifs:
    """Table datée du socle (fixture partagée) : trois modèles, Standard + Flex."""
    return TableTarifs(tarifs)


def _agregat(
    jour: date,
    requetes: int,
    cout_reel: float,
    *,
    modele: str = GRAND_MODELE,
    jetons: int = JETONS_PAR_REQUETE,
) -> AgregatQuotidien:
    """Agrégat quotidien du mini-jeu (jetons = requetes × `jetons`)."""
    return AgregatQuotidien(
        tenant_id=TENANT,
        application_id=APPLICATION,
        equipe=EQUIPE,
        environnement="production",
        modele_id=modele,
        jour=jour,
        nombre_requetes=requetes,
        jetons_entree_total=requetes * jetons,
        jetons_sortie_total=requetes * jetons,
        cout_recalcule_usd=cout_reel,
    )


def _agregats() -> list[AgregatQuotidien]:
    """Les trois jours du mini-jeu."""
    return [
        _agregat(jour, requetes, cout)
        for jour, requetes, cout in zip(JOURS, REQUETES_PAR_JOUR, COUT_REEL_PAR_JOUR, strict=True)
    ]


def _lancer(table: TableTarifs, volumes=None, **surcharges) -> ResultatBacktest:
    """Backtest du mini-jeu, avec surcharges possibles."""
    parametres: dict[str, object] = {
        "tarifs": table,
        "region": REGION,
        "modele_candidat": PETIT_MODELE,
        "volumes": volumes_depuis_agregats(_agregats()) if volumes is None else volumes,
        "tenant_id": TENANT,
        "application_id": APPLICATION,
        "periode_debut": JOURS[0],
        "periode_fin": JOURS[-1],
    }
    parametres.update(surcharges)
    return backtester(**parametres)  # type: ignore[arg-type]


def test_economie_exacte_verifiee_a_la_main(table: TableTarifs) -> None:
    """Les totaux reproduisent exactement le calcul fait à la main."""
    resultat = _lancer(table)
    assert resultat.cout_reel_usd == pytest.approx(5.4)
    assert resultat.cout_contrefactuel_usd == pytest.approx(0.288)
    assert resultat.economie_usd == pytest.approx(5.112)
    assert resultat.economie_pourcentage == pytest.approx(94.67)
    assert resultat.cout_par_requete_reel_usd == pytest.approx(0.09)
    assert resultat.cout_par_requete_contrefactuel_usd == pytest.approx(0.0048)
    assert resultat.economie_projection_mensuelle_usd == pytest.approx(51.12)


def test_volume_constant_le_nombre_de_requetes_ne_bouge_pas(table: TableTarifs) -> None:
    """Le backtest rejoue les tarifs, jamais le volume (EF-VER-01)."""
    resultat = _lancer(table)
    assert resultat.nombre_requetes == sum(REQUETES_PAR_JOUR)
    assert resultat.jours_observes == 3
    # À volume constant, le ratio des coûts par requête vaut le ratio des tarifs.
    assert resultat.cout_par_requete_contrefactuel_usd == pytest.approx(0.0048)
    assert resultat.cout_par_requete_reel_usd == pytest.approx(0.09)


def test_ventilation_par_dimension_et_par_jour(table: TableTarifs) -> None:
    """L'économie est ventilée par application, équipe, modèle et jour."""
    resultat = _lancer(table)
    assert resultat.ventilation.par_application[APPLICATION] == pytest.approx(5.112)
    assert resultat.ventilation.par_equipe[EQUIPE] == pytest.approx(5.112)
    assert resultat.ventilation.par_modele[GRAND_MODELE] == pytest.approx(5.112)
    assert resultat.ventilation.par_jour[JOURS[0]] == pytest.approx(ECONOMIE_PAR_JOUR[0])
    assert resultat.ventilation.par_jour[JOURS[1]] == pytest.approx(ECONOMIE_PAR_JOUR[1])
    assert resultat.ventilation.par_jour[JOURS[2]] == pytest.approx(ECONOMIE_PAR_JOUR[2])
    assert sum(resultat.ventilation.par_jour.values()) == pytest.approx(resultat.economie_usd)


def test_intervalles_ordonnes_et_disperses(table: TableTarifs) -> None:
    """P10 ≤ P50 ≤ P90, avec une dispersion réelle (pas un point déguisé)."""
    resultat = _lancer(table)
    assert resultat.economie_p10_usd <= resultat.economie_p50_usd <= resultat.economie_p90_usd
    assert resultat.economie_p10_usd < resultat.economie_p90_usd
    # La médiane du bootstrap reste proche du total observé.
    assert resultat.economie_p50_usd == pytest.approx(resultat.economie_usd, rel=0.25)


def test_bootstrap_reproductible_a_graine_constante(table: TableTarifs) -> None:
    """Même graine ⇒ mêmes percentiles : le résultat est reproductible."""
    premier = _lancer(table, graine=7)
    second = _lancer(table, graine=7)
    assert (premier.economie_p10_usd, premier.economie_p90_usd) == (
        second.economie_p10_usd,
        second.economie_p90_usd,
    )


def test_tarif_candidat_absent_erreur_explicite(table: TableTarifs) -> None:
    """Un candidat absent de la table datée lève `TarifInconnuError`, jamais 0 $."""
    with pytest.raises(TarifInconnuError) as erreur:
        _lancer(table, modele_candidat=MODELE_CHER)
    assert erreur.value.details["modele"] == MODELE_CHER


def test_periode_sans_donnees_resultat_vide_explicite(table: TableTarifs) -> None:
    """Aucun volume dans la période : résultat vide explicite, aucune division."""
    resultat = _lancer(table, periode_debut=date(2020, 1, 1), periode_fin=date(2020, 1, 31))
    assert resultat.donnees_absentes is True
    assert resultat.nombre_requetes == 0
    assert resultat.jours_observes == 0
    assert (resultat.cout_reel_usd, resultat.cout_contrefactuel_usd) == (0.0, 0.0)
    assert (resultat.economie_usd, resultat.economie_pourcentage) == (0.0, 0.0)
    assert (resultat.economie_p10_usd, resultat.economie_p50_usd, resultat.economie_p90_usd) == (
        0.0,
        0.0,
        0.0,
    )
    assert resultat.economie_projection_mensuelle_usd == 0.0


def test_entrees_vides_resultat_vide(table: TableTarifs) -> None:
    """Une liste de volumes vide rend le même résultat vide explicite."""
    resultat = _lancer(table, volumes=[])
    assert resultat.donnees_absentes is True
    assert resultat.requetes_non_evaluables == 0


def test_jetons_manquants_requete_exclue_et_comptee(table: TableTarifs) -> None:
    """Jetons illisibles : le volume est exclu **et** compté, jamais valué à 0 $."""
    volumes = [
        VolumeBacktest(
            jour=JOURS[0],
            application_id=APPLICATION,
            equipe=EQUIPE,
            modele_id=GRAND_MODELE,
            nombre_requetes=10,
            jetons_entree=10_000,
            jetons_sortie=10_000,
            cout_reel_usd=0.9,
        ),
        VolumeBacktest(
            jour=JOURS[1],
            application_id=APPLICATION,
            equipe=EQUIPE,
            modele_id=GRAND_MODELE,
            nombre_requetes=5,
            jetons_entree=None,
            jetons_sortie=None,
            cout_reel_usd=0.45,
        ),
    ]
    resultat = _lancer(table, volumes=volumes)
    assert resultat.requetes_non_evaluables == 5
    assert resultat.nombre_requetes == 10
    assert resultat.cout_reel_usd == pytest.approx(0.9)
    assert resultat.economie_usd == pytest.approx(0.852)


def test_agregat_sans_jetons_marque_non_evaluable() -> None:
    """L'adaptateur marque « jetons inconnus » un agrégat sans consommation."""
    agregats = _agregats() + [_agregat(JOURS[0], 4, 0.0, modele=MOYEN_MODELE, jetons=0)]
    volumes = volumes_depuis_agregats(agregats)
    assert volumes[-1].jetons_connus is False
    assert volumes[0].jetons_connus is True


def test_candidat_plus_cher_economie_negative(table: TableTarifs) -> None:
    """Un candidat plus cher rend une économie négative : jamais arrondie à zéro."""
    table.ajouter(
        TarifModele(
            modele_id=MODELE_CHER,
            region=REGION,
            niveau=ServiceTier.STANDARD,
            date_effet=date(2026, 1, 1),
            prix_entree_1k=0.03,
            prix_sortie_1k=0.15,
        )
    )
    resultat = _lancer(table, modele_candidat=MODELE_CHER)
    # 60 × (0,03 + 0,15) = 10,80 $ ; économie = 5,40 − 10,80 = −5,40 $.
    assert resultat.cout_contrefactuel_usd == pytest.approx(10.8)
    assert resultat.economie_usd == pytest.approx(-5.4)
    assert resultat.economie_pourcentage == pytest.approx(-100.0)


def test_couts_contrefactuels_par_jour(table: TableTarifs) -> None:
    """Le tarif du candidat est bien celui de la table datée, jour par jour."""
    resultat = _lancer(table)
    for jour, attendu in zip(JOURS, COUT_CANDIDAT_PAR_JOUR, strict=True):
        cout_reel = dict(zip(JOURS, COUT_REEL_PAR_JOUR, strict=True))[jour]
        assert resultat.ventilation.par_jour[jour] == pytest.approx(cout_reel - attendu)


def test_hypotheses_portees_dans_le_resultat(table: TableTarifs) -> None:
    """L'hypothèse « mêmes requêtes, mêmes jetons » voyage avec le chiffre."""
    hypotheses = _lancer(table).hypotheses
    assert hypotheses.volume_constant is True
    assert hypotheses.memes_jetons is True
    assert hypotheses.qualite_non_verifiee is True
    assert hypotheses.niveau_candidat == ServiceTier.STANDARD.value
    assert hypotheses.mention == MENTION_PROJECTION
    assert any("mêmes requêtes" in ligne for ligne in hypotheses.liste)
    assert any("qualité" in ligne for ligne in hypotheses.liste)


def test_entrees_et_resultat_sans_texte_de_requete() -> None:
    """ENF-CONF-01 : aucune entrée ni aucun résultat ne porte de texte."""
    champs_entree = {champ.name for champ in fields(VolumeBacktest)}
    champs_resultat = {champ.name for champ in fields(ResultatBacktest)}
    for champs in (champs_entree, champs_resultat):
        assert "texte_requete" not in champs
        assert "reponse_candidate" not in champs
        assert not any("texte" in nom for nom in champs)


@pytest.mark.parametrize(
    ("surcharges", "message"),
    [
        ({"modele_candidat": "   "}, "modèle candidat"),
        ({"periode_debut": date(2026, 11, 1), "periode_fin": date(2026, 10, 1)}, "précède"),
        ({"iterations_bootstrap": 0}, "bootstrap"),
    ],
)
def test_demande_incoherente_refusee(
    table: TableTarifs, surcharges: dict[str, object], message: str
) -> None:
    """Une demande incohérente est refusée avant tout calcul."""
    with pytest.raises(ValidationError) as erreur:
        _lancer(table, **surcharges)
    assert message.lower() in str(erreur.value).lower()
