"""Tests des étiquettes d'attribution et de la part non attribuée (EF-ATT-03, EF-ATT-07).

Trois choses sont vérifiées ici : l'extraction tolérante des métadonnées de
requête, l'application par défaut pour les appels non étiquetés (§8.1) et le
chiffrage de la dépense non attribuée avec ses causes (§9.1).
"""

from __future__ import annotations

import pytest

from jauge.attribution.tags import (
    CAUSE_APPLICATION_HORS_SCHEMA,
    CAUSE_POINT_ACCES_NON_JOURNALISE,
    CAUSE_SANS_ETIQUETTE,
    VALEUR_INCONNUE,
    Etiquettes,
    LigneAttribution,
    PointAcces,
    appliquer_schema,
    calculer_part_non_attribuee,
    completer,
    extraire_etiquettes,
    valeurs_hors_schema,
)
from jauge.models.tenant import Application, SchemaAttribution


def test_extraction_des_quatre_dimensions() -> None:
    """Les quatre dimensions du schéma sont lues dans les métadonnées."""
    etiquettes = extraire_etiquettes(
        {
            "application": "ticket-classifier",
            "team": "support",
            "environment": "production",
            "requestType": "classement",
        }
    )

    assert etiquettes.application == "ticket-classifier"
    assert etiquettes.equipe == "support"
    assert etiquettes.environnement == "production"
    assert etiquettes.type_requete == "classement"
    assert etiquettes.completes is True
    assert etiquettes.dimensions_manquantes() == []


@pytest.mark.parametrize(
    "cle",
    ["application", "applicationId", "application_id", "app", "inferenceProfile", "projectName"],
)
def test_variantes_du_nom_d_application(cle: str) -> None:
    """Plusieurs orthographes désignent la même étiquette d'application."""
    assert extraire_etiquettes({cle: "app-x"}).application == "app-x"


def test_metadonnees_imbriquees_et_prefixees() -> None:
    """Une étiquette imbriquée ou préfixée (`user:application`) est reconnue."""
    etiquettes = extraire_etiquettes(
        {"jauge": {"application": "resume-nocturne", "team": "data"}, "user:environment": "dev"}
    )

    assert etiquettes.application == "resume-nocturne"
    assert etiquettes.equipe == "data"
    assert etiquettes.environnement == "dev"


def test_valeurs_vides_ignorees() -> None:
    """Une étiquette vide n'est pas une étiquette : elle vaut absence."""
    etiquettes = extraire_etiquettes({"application": "   ", "team": ""})

    assert etiquettes.application is None
    assert etiquettes.equipe is None
    assert etiquettes.est_attribuee is False


def test_metadonnees_absentes() -> None:
    """Pas de métadonnées du tout : aucune étiquette, aucune exception."""
    assert extraire_etiquettes(None) == Etiquettes()
    assert extraire_etiquettes({}) == Etiquettes()


def test_application_par_defaut_pour_appel_non_etiquete(schema_attribution) -> None:
    """Un appel sans étiquette tombe sur l'application par défaut (EF-ATT-03)."""
    etiquettes = appliquer_schema(Etiquettes(), schema_attribution)

    assert etiquettes.application == "non-attribue"
    assert etiquettes.est_attribuee is True  # la dépense reste rattachée à une ligne


def test_application_par_defaut_ne_remplace_pas_une_etiquette() -> None:
    """Une étiquette présente est conservée telle quelle."""
    etiquettes = appliquer_schema(Etiquettes(application="ticket-classifier"), SchemaAttribution())

    assert etiquettes.application == "ticket-classifier"


def test_completion_depuis_la_fiche_application(applications: list[Application]) -> None:
    """Équipe et environnement viennent de la fiche quand l'appel les omet."""
    app = applications[0]
    etiquettes = completer(Etiquettes(application=app.id), app)

    assert etiquettes.equipe == "support"
    assert etiquettes.environnement == "production"


def test_completion_sans_fiche_marque_l_inconnu() -> None:
    """Sans fiche d'application, la dimension vaut explicitement « inconnue »."""
    etiquettes = completer(Etiquettes(application="app-inconnue"), None)

    assert etiquettes.equipe == VALEUR_INCONNUE
    assert etiquettes.environnement == VALEUR_INCONNUE


def test_valeurs_hors_schema_signalees(schema_attribution) -> None:
    """Une équipe ou un environnement inconnu du schéma est signalé, pas corrigé."""
    anomalies = valeurs_hors_schema(
        Etiquettes(application="app-x", equipe="marketing", environnement="production"),
        schema_attribution,
    )

    assert anomalies == ["equipe=marketing"]


def test_valeurs_conformes_au_schema(schema_attribution) -> None:
    """Une valeur autorisée ne produit aucune anomalie."""
    assert (
        valeurs_hors_schema(
            Etiquettes(application="app-x", equipe="support", environnement="staging"),
            schema_attribution,
        )
        == []
    )


def test_part_non_attribuee_et_causes() -> None:
    """Un appel sans étiquette est chiffré et sa cause est nommée (EF-ATT-07)."""
    bilan = calculer_part_non_attribuee(
        [
            LigneAttribution(etiquettes=Etiquettes(application="app-a"), cout_usd=0.90),
            LigneAttribution(etiquettes=Etiquettes(), cout_usd=0.10, etiquete=False),
        ]
    )

    assert bilan.depense_totale_usd == pytest.approx(1.0)
    assert bilan.depense_non_attribuee_usd == pytest.approx(0.10)
    assert bilan.part_pourcentage == pytest.approx(10.0)
    assert bilan.causes == {CAUSE_SANS_ETIQUETTE: pytest.approx(0.10)}
    assert bilan.libelles() == ["appels sans étiquette d'application : 0.10 USD"]


def test_part_non_attribuee_point_acces_non_journalise() -> None:
    """La dépense d'un appel `bedrock-mantle` compte comme non attribuée."""
    bilan = calculer_part_non_attribuee(
        [
            LigneAttribution(
                etiquettes=Etiquettes(application="app-a"),
                cout_usd=0.5,
                point_acces=PointAcces.MANTLE,
            )
        ]
    )

    assert bilan.part_pourcentage == pytest.approx(100.0)
    assert CAUSE_POINT_ACCES_NON_JOURNALISE in bilan.causes


def test_part_non_attribuee_hors_schema() -> None:
    """Une application hors schéma d'attribution est signalée comme cause."""
    bilan = calculer_part_non_attribuee(
        [
            LigneAttribution(
                etiquettes=Etiquettes(application="app-inconnue"),
                cout_usd=0.25,
                hors_schema=True,
            )
        ]
    )

    assert bilan.causes == {CAUSE_APPLICATION_HORS_SCHEMA: pytest.approx(0.25)}


def test_depense_non_journalisee_ajoutee_au_total() -> None:
    """La dépense invisible des journaux entre au dénominateur et au numérateur."""
    bilan = calculer_part_non_attribuee(
        [LigneAttribution(etiquettes=Etiquettes(application="app-a"), cout_usd=0.75)],
        depense_non_journalisee_usd=0.25,
    )

    assert bilan.depense_totale_usd == pytest.approx(1.0)
    assert bilan.part_pourcentage == pytest.approx(25.0)
    assert bilan.causes == {CAUSE_POINT_ACCES_NON_JOURNALISE: pytest.approx(0.25)}


def test_part_non_attribuee_sans_depense() -> None:
    """Aucune dépense : la part vaut 0 %, sans division par zéro."""
    bilan = calculer_part_non_attribuee([])

    assert bilan.part_pourcentage == 0.0
    assert bilan.causes == {}
    assert bilan.libelles() == []


def test_part_non_attribuee_bornee_a_100() -> None:
    """Une dépense non attribuée supérieure au total reste bornée à 100 %."""
    bilan = calculer_part_non_attribuee(
        [LigneAttribution(etiquettes=Etiquettes(), cout_usd=0.10, etiquete=False)],
        depense_non_journalisee_usd=5.0,
    )

    assert bilan.part_pourcentage == pytest.approx(100.0)
