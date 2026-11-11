"""Tests des cas limites et de la tolérance bas niveau (EF-ATT-05, §23 Q2).

Ces tests couvrent les branches défensives des utilitaires : ce sont elles qui
permettent à la chaîne d'attribution de tourner sur un journal réel, imparfait
(ligne tronquée, type inattendu, valeur vide, epoch absurde) sans jamais lever.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from jauge.attribution.champs import aplatir, booleen, entier, normaliser_cle, texte, valeur
from jauge.attribution.logs import (
    EnregistrementInexploitable,
    analyser_enregistrement,
    iterer_enregistrements,
    iterer_lignes,
    lire_journaux,
)
from jauge.attribution.reconcile import (
    FactureJour,
    facture_par_jour,
    iterer_lignes_cur,
    lire_cur,
    lire_fichier_cur,
    parser_ligne_cur,
)
from jauge.attribution.tags import Etiquettes, PointAcces
from jauge.attribution.valeurs import (
    determiner_niveau_service,
    determiner_point_acces,
    lire_horodatage,
)
from jauge.models.pricing import ServiceTier

#: Ligne CUR minimale valide, pour les tests de tolérance.
LIGNE_CUR_MINIMALE = {
    "line_item_usage_start_date": "2026-11-09T00:00:00Z",
    "line_item_unblended_cost": "0.0100",
}


def test_normaliser_cle_est_stable_et_memorisee() -> None:
    """Trois orthographes donnent la même clé, et le cache rend le même objet."""
    assert normaliser_cle("inputTokenCount") == "inputtokencount"
    assert normaliser_cle("input_token_count") == "inputtokencount"
    assert normaliser_cle("Input-Token-Count") == "inputtokencount"
    assert normaliser_cle("inputTokenCount") is normaliser_cle("inputTokenCount")


def test_aplatir_ignore_les_listes() -> None:
    """Les listes (contenu des messages) ne sont jamais recopiées (ENF-CONF-01)."""
    plat = aplatir({"messages": [{"role": "user", "content": "secret"}], "modelId": "m"})

    assert plat == {"modelid": "m"}


def test_aplatir_conserve_chemin_et_nom_court() -> None:
    """Un objet imbriqué expose son chemin complet et son nom court."""
    plat = aplatir({"usage": {"inputTokens": 12}})

    assert plat["usageinputtokens"] == 12
    assert plat["inputtokens"] == 12


def test_aplatir_gere_un_objet_imbrique_vide() -> None:
    """Un objet imbriqué vide est conservé comme valeur, pas déplié."""
    plat = aplatir({"metadata": {}, "modelId": "m"})

    assert plat["metadata"] == {}
    assert plat["modelid"] == "m"


def test_lecteurs_de_champs_sur_absence() -> None:
    """Sur un dictionnaire vide, tous les lecteurs rendent une valeur neutre."""
    assert valeur({}, ("x",)) is None
    assert texte({}, ("x",)) is None
    assert entier({}, ("x",)) is None
    assert booleen({}, ("x",)) is False


def test_entier_refuse_les_valeurs_non_numeriques() -> None:
    """Un compteur textuel illisible rend `None` plutôt qu'une exception."""
    assert entier({"x": "beaucoup"}, ("x",)) is None
    assert entier({"x": True}, ("x",)) is None
    assert entier({"x": "-5"}, ("x",)) == 0  # jamais négatif


def test_booleen_accepte_un_vrai_booleen() -> None:
    """Un drapeau déjà booléen est rendu tel quel."""
    assert booleen({"x": True}, ("x",)) is True
    assert booleen({"x": False}, ("x",)) is False


def test_texte_accepte_un_nombre() -> None:
    """Une étiquette numérique devient son texte (identifiant d'équipe…)."""
    assert texte({"x": 42}, ("x",)) == "42"


def test_analyser_refuse_un_type_inattendu() -> None:
    """Un type inattendu (liste, entier) est rejeté avec un motif clair."""
    with pytest.raises(EnregistrementInexploitable, match="type d'enregistrement inattendu"):
        analyser_enregistrement([1, 2, 3])


def test_analyser_refuse_un_json_non_objet() -> None:
    """Un JSON valide mais non objet (liste, scalaire) est rejeté."""
    with pytest.raises(EnregistrementInexploitable, match="non objet"):
        analyser_enregistrement("[1, 2, 3]")


def test_analyser_accepte_des_octets() -> None:
    """Une ligne lue en binaire (S3) est décodée avant analyse."""
    brut = json.dumps(
        {
            "timestamp": "2026-11-09T08:00:00Z",
            "modelId": "m",
            "inputTokenCount": 10,
            "outputTokenCount": 1,
        }
    ).encode()

    assert analyser_enregistrement(brut).model_id == "m"


def test_rejet_conserve_les_cles_du_brut() -> None:
    """Un rejet conserve les clés de l'enregistrement : diagnostic possible."""
    rapport = None
    from jauge.attribution.logs import RapportLecture

    rapport = RapportLecture()
    list(iterer_enregistrements([{"modelId": "m", "timestamp": "pas une date"}], rapport))

    assert rapport.rejetes[0].champs == ["modelId", "timestamp"]
    assert rapport.rejetes[0].index == 0


def test_cles_vides_pour_un_brut_illisible() -> None:
    """Un brut illisible n'a pas de clés : la liste est vide, pas une erreur."""
    from jauge.attribution.logs import RapportLecture

    rapport = RapportLecture()
    list(iterer_enregistrements(["pas du json"], rapport))

    assert rapport.rejetes[0].champs == []


def test_iterer_lignes_filtre_les_vides() -> None:
    """Les lignes vides d'un flux texte sont ignorées."""
    assert list(iterer_lignes(["a\n", "\n", "  \n", "b\n"])) == ["a", "b"]


def test_lire_journaux_sans_rapport(tmp_path: Path) -> None:
    """Le rapport est optionnel : la lecture fonctionne sans observabilité."""
    fichier = tmp_path / "j.jsonl"
    fichier.write_text(
        json.dumps(
            {
                "timestamp": "2026-11-09T08:00:00Z",
                "modelId": "m",
                "inputTokenCount": 1,
                "outputTokenCount": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert len(list(lire_journaux(fichier))) == 1


@pytest.mark.parametrize(
    ("valeur_entree", "attendu"),
    [
        (datetime(2026, 11, 9, 8, 0), datetime(2026, 11, 9, 8, 0, tzinfo=UTC)),
        (True, None),
        ("", None),
        ("pas une date", None),
        ("1e400", None),
    ],
)
def test_horodatages_limites(valeur_entree: object, attendu: datetime | None) -> None:
    """Chaque forme d'horodatage douteuse rend `None`, jamais une exception."""
    assert lire_horodatage(valeur_entree) == attendu


def test_horodatage_datetime_avec_fuseau() -> None:
    """Un `datetime` déjà daté est rendu inchangé."""
    moment = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)

    assert lire_horodatage(moment) is moment


def test_point_acces_vide() -> None:
    """Une valeur de point d'accès vide vaut `bedrock-runtime` (journalisé)."""
    assert determiner_point_acces("") is PointAcces.RUNTIME


def test_niveau_service_vide() -> None:
    """Un niveau vide vaut Standard."""
    assert determiner_niveau_service("") is ServiceTier.STANDARD


def test_ligne_cur_non_json_ignoree(tmp_path: Path) -> None:
    """Une ligne d'export illisible est comptée, pas fatale."""
    fichier = tmp_path / "cur.jsonl"
    fichier.write_text("pas du json\n" + json.dumps(LIGNE_CUR_MINIMALE) + "\n", encoding="utf-8")

    from jauge.attribution.reconcile import StatistiquesCur

    stats = StatistiquesCur()
    lignes = list(lire_fichier_cur(fichier, stats))

    assert len(lignes) == 1
    assert stats.ignorees_illisibles == 1


def test_lire_cur_ignore_les_lignes_vides(tmp_path: Path) -> None:
    """Un export avec lignes vides ne produit pas de faux rejets."""
    fichier = tmp_path / "cur.jsonl"
    fichier.write_text("\n" + json.dumps(LIGNE_CUR_MINIMALE) + "\n\n", encoding="utf-8")

    assert len(list(lire_cur(fichier.open(encoding="utf-8")))) == 1


def test_ligne_cur_json_non_objet_ignoree() -> None:
    """Un JSON valide mais non objet est ignoré proprement."""
    from jauge.attribution.reconcile import StatistiquesCur

    stats = StatistiquesCur()
    assert list(iterer_lignes_cur(["[]"], stats)) == []
    assert stats.ignorees_illisibles == 1


def test_jour_cur_en_datetime_et_datetime_naif() -> None:
    """La date d'usage est lue depuis un `datetime`, avec ou sans fuseau."""
    avec = parser_ligne_cur(
        {**LIGNE_CUR_MINIMALE, "line_item_usage_start_date": datetime(2026, 11, 9, tzinfo=UTC)}
    )
    sans = parser_ligne_cur(
        {**LIGNE_CUR_MINIMALE, "line_item_usage_start_date": datetime(2026, 11, 9, 23, 0)}
    )

    assert avec.jour == date(2026, 11, 9)
    assert sans.jour == date(2026, 11, 9)


def test_jour_cur_deja_en_date() -> None:
    """Un objet `date` est accepté directement."""
    ligne = parser_ligne_cur(
        {**LIGNE_CUR_MINIMALE, "line_item_usage_start_date": date(2026, 11, 9)}
    )

    assert ligne.jour == date(2026, 11, 9)


def test_montant_avec_devise_et_virgule() -> None:
    """Un montant formaté (`1 234,56 $`) reste lisible."""
    ligne = parser_ligne_cur({**LIGNE_CUR_MINIMALE, "line_item_unblended_cost": "1234,56$"})

    assert ligne.montant_usd == pytest.approx(1234.56)


def test_facture_jour_sans_depense() -> None:
    """Une facture nulle rend une part non attribuée de 0 %, sans division par zéro."""
    facture = FactureJour(
        jour=date(2026, 11, 9), cout_total_usd=0.0, cout_par_etiquette={}, cout_non_attribue_usd=0.0
    )

    assert facture.part_non_attribuee_pourcentage == 0.0


def test_facture_par_jour_avec_ligne_sans_etiquette() -> None:
    """Une ligne sans étiquette alimente la part non attribuée du jour."""
    lignes = list(
        iterer_lignes_cur(
            [LIGNE_CUR_MINIMALE, {**LIGNE_CUR_MINIMALE, "line_item_unblended_cost": "0.5"}]
        )
    )

    facture = facture_par_jour(lignes)[date(2026, 11, 9)]

    assert facture.cout_non_attribue_usd == pytest.approx(0.51)
    assert facture.part_non_attribuee_pourcentage == pytest.approx(100.0)


def test_etiquettes_absentes_dans_les_metadonnees() -> None:
    """Une métadonnée sans dimension connue rend des étiquettes vides."""
    from jauge.attribution.tags import extraire_etiquettes

    assert extraire_etiquettes({"autre": "valeur"}) == Etiquettes()
