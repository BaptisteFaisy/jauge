"""Tests de la preuve par rejeu d'une variante de prompt (piste n° 8).

Aucun réseau, aucun AWS : le client de modèle et le juge sont les adaptateurs
fictifs du socle. On vérifie qu'une variante de prompt est traitée comme un
candidat ordinaire — mêmes seuils, mêmes contrôles déterministes (§13.4) — et
que le gain net à 12 mois conditionne la proposition.
"""

from __future__ import annotations

import json

import pytest
from tests.replay.constantes import APPLICATION, DATE_EFFET, MODELE_GRAND, MODELE_JUGE, REGION_REJEU
from tests.replay.test_variantes import CONSIGNE_A_CONTRAINTE_DANS_CONTEXTE, CONSIGNE_VERBEUSE

from jauge.evaluation.judge import FakeJugeClient, VerdictJuge
from jauge.models.campaign import EchantillonRequete, ParametresRejeu
from jauge.models.tenant import Application
from jauge.replay.client import FakeModeleClient
from jauge.replay.executor import TextesEnMemoire
from jauge.replay.variantes import generer_variantes
from jauge.replay.variantes_preuve import PreuveVariante, prouver_variante

# 40 requêtes : avec un taux d'équivalence de 100 %, la borne basse de Wilson
# dépasse 90 % (condition 2 du §13.4) à partir de 35 requêtes.
TAILLE_ECHANTILLON = 40
JETONS_ENTREE_ORIGINE = 600
VOLUME_RENTABLE = 200_000
VOLUME_NON_RENTABLE = 500
TEXTE_REQUETE = "Ticket client : " + "texte " * 60


@pytest.fixture
def application() -> Application:
    """Application de démonstration, seuils par défaut du §13.4."""
    return Application(
        id=APPLICATION,
        tenant_id="t-demo-a",
        nom="Classement de tickets",
        equipe="support",
        modele_actuel=MODELE_GRAND,
    )


@pytest.fixture
def echantillon() -> tuple[EchantillonRequete, ...]:
    """Douze requêtes « general » : contrôles déterministes bornés à la longueur."""
    return tuple(
        EchantillonRequete(
            requete_id=f"req-{index:04d}",
            application_id=APPLICATION,
            type_requete="general",
            longueur_caracteres=len(TEXTE_REQUETE),
            jetons_entree_origine=JETONS_ENTREE_ORIGINE,
            stratum="moyen:general",
        )
        for index in range(TAILLE_ECHANTILLON)
    )


@pytest.fixture
def textes(echantillon: tuple[EchantillonRequete, ...]) -> TextesEnMemoire:
    """Textes du plan de données : jamais visibles du plan de contrôle."""
    return TextesEnMemoire({requete.requete_id: TEXTE_REQUETE for requete in echantillon})


@pytest.fixture
def variante_exemples():
    """Variante « suppression des exemples redondants », acceptée par le garde-fou."""
    variantes = generer_variantes(CONSIGNE_VERBEUSE, application_id=APPLICATION)
    return next(
        variante
        for variante in variantes
        if variante.transformation == "suppression_exemples_redondants"
    )


@pytest.fixture
def variante_refusee():
    """Variante refusée à la génération : elle effacerait une contrainte."""
    variantes = generer_variantes(
        CONSIGNE_A_CONTRAINTE_DANS_CONTEXTE, application_id=APPLICATION, seuil_contexte=40
    )
    return next(variante for variante in variantes if not variante.acceptee)


def _prouver(
    application: Application,
    variante,
    consigne: str,
    echantillon,
    textes: TextesEnMemoire,
    client: FakeModeleClient,
    table_tarifs,
    juge: FakeJugeClient,
    volume_mensuel: int,
) -> PreuveVariante:
    """Lance la preuve avec les paramètres de démonstration."""
    return prouver_variante(
        application=application,
        variante=variante,
        consigne_originale=consigne,
        client=client,
        tarifs=table_tarifs,
        region=REGION_REJEU,
        a_la_date=DATE_EFFET,
        echantillon=echantillon,
        textes=textes,
        parametres=ParametresRejeu(),
        juge=juge,
        volume_mensuel=volume_mensuel,
        ajuster_jetons=True,
    )


def test_variante_qui_tient_la_qualite_est_proposee(
    application, variante_exemples, echantillon, textes, client_fictif, table_tarifs
) -> None:
    """Qualité tenue (juge favorable) et gain net positif ⇒ variante proposée."""
    preuve = _prouver(
        application,
        variante_exemples,
        CONSIGNE_VERBEUSE,
        echantillon,
        textes,
        client_fictif,
        table_tarifs,
        FakeJugeClient(modele_id=MODELE_JUGE),
        VOLUME_RENTABLE,
    )

    assert preuve.proposee
    assert preuve.motifs == ()
    assert preuve.cout_par_requete_apres < preuve.cout_par_requete_avant
    assert preuve.economie_mensuelle_usd > 0
    assert preuve.gain_net_12_mois_usd > 0
    assert preuve.jetons_economises > 0


def test_la_formule_du_gain_net_est_appliquee(
    application, variante_exemples, echantillon, textes, client_fictif, table_tarifs
) -> None:
    """gain net = économie mensuelle mesurée × 12 − coût de la campagne de preuve."""
    preuve = _prouver(
        application,
        variante_exemples,
        CONSIGNE_VERBEUSE,
        echantillon,
        textes,
        client_fictif,
        table_tarifs,
        FakeJugeClient(modele_id=MODELE_JUGE),
        VOLUME_RENTABLE,
    )

    attendu = round(preuve.economie_mensuelle_usd * 12 - preuve.cout_preuve_usd, 2)
    assert preuve.gain_net_12_mois_usd == pytest.approx(attendu)
    assert preuve.cout_preuve_usd > 0


def test_la_decision_utilise_les_trois_conditions_du_paragraphe_13_4(
    application, variante_exemples, echantillon, textes, client_fictif, table_tarifs
) -> None:
    """Mêmes seuils et mêmes contrôles qu'un changement de modèle."""
    preuve = _prouver(
        application,
        variante_exemples,
        CONSIGNE_VERBEUSE,
        echantillon,
        textes,
        client_fictif,
        table_tarifs,
        FakeJugeClient(modele_id=MODELE_JUGE),
        VOLUME_RENTABLE,
    )

    assert preuve.decision is not None
    assert [condition.nom for condition in preuve.decision.conditions] == [
        "taux_equivalence",
        "intervalle_confiance_bas",
        "controles_deterministes",
    ]
    assert preuve.decision.resultat is not None
    assert preuve.decision.resultat.nombre_requetes_rejouees == TAILLE_ECHANTILLON


def test_variante_qui_degrade_la_qualite_est_rejetee_avec_motif(
    application, variante_exemples, echantillon, textes, client_fictif, table_tarifs
) -> None:
    """Juge défavorable sur tout l'échantillon ⇒ rejet motivé, jamais proposé."""
    juge = FakeJugeClient(
        modele_id=MODELE_JUGE,
        verdict_defaut=VerdictJuge(equivalent=False, explication="service rendu différent"),
    )

    preuve = _prouver(
        application,
        variante_exemples,
        CONSIGNE_VERBEUSE,
        echantillon,
        textes,
        client_fictif,
        table_tarifs,
        juge,
        VOLUME_RENTABLE,
    )

    assert not preuve.proposee
    assert any("taux_equivalence" in motif for motif in preuve.motifs)
    assert any("intervalle_confiance_bas" in motif for motif in preuve.motifs)


def test_gain_net_negatif_variante_non_proposee(
    application, variante_exemples, echantillon, textes, client_fictif, table_tarifs
) -> None:
    """Volume trop faible : la preuve coûte plus cher que le gain sur 12 mois."""
    preuve = _prouver(
        application,
        variante_exemples,
        CONSIGNE_VERBEUSE,
        echantillon,
        textes,
        client_fictif,
        table_tarifs,
        FakeJugeClient(modele_id=MODELE_JUGE),
        VOLUME_NON_RENTABLE,
    )

    assert preuve.economie_mensuelle_usd > 0
    assert not preuve.proposee
    assert preuve.gain_net_12_mois_usd <= 0
    assert any("gain net" in motif for motif in preuve.motifs)


def test_variante_refusee_n_est_pas_rejouee(
    application, variante_refusee, echantillon, textes, client_fictif, table_tarifs
) -> None:
    """Aucun dollar dépensé pour une variante qui casse une contrainte métier."""
    preuve = _prouver(
        application,
        variante_refusee,
        CONSIGNE_A_CONTRAINTE_DANS_CONTEXTE,
        echantillon,
        textes,
        client_fictif,
        table_tarifs,
        FakeJugeClient(modele_id=MODELE_JUGE),
        VOLUME_RENTABLE,
    )

    assert not preuve.proposee
    assert preuve.refus
    assert any("refusée à la génération" in motif for motif in preuve.motifs)
    assert client_fictif.appels == []


def test_aucun_fragment_de_prompt_ne_remonte_au_plan_de_controle(
    application, variante_exemples, echantillon, textes, client_fictif, table_tarifs
) -> None:
    """ENF-CONF-01 : la vue de contrôle ne contient ni prompt, ni diff, ni réponse."""
    preuve = _prouver(
        application,
        variante_exemples,
        CONSIGNE_VERBEUSE,
        echantillon,
        textes,
        client_fictif,
        table_tarifs,
        FakeJugeClient(modele_id=MODELE_JUGE),
        VOLUME_RENTABLE,
    )

    transmis = json.dumps(preuve.vers_controle(), ensure_ascii=False)
    for fragment in (
        "assistant de classement",
        "Exemple 1",
        "Ne jamais divulguer",
        "<contexte>",
        "Historique du client",
        TEXTE_REQUETE,
        "reponse-",
        "consigne-originale",
    ):
        assert fragment not in transmis


def test_preuve_reproductible(
    application, variante_exemples, echantillon, textes, table_tarifs
) -> None:
    """Deux preuves identiques : mêmes jetons, même gain net (ENF-IA-01)."""
    base = (application, variante_exemples, CONSIGNE_VERBEUSE, echantillon, textes)
    juge = FakeJugeClient(modele_id=MODELE_JUGE)

    premiere = _prouver(
        *base,
        FakeModeleClient(jetons_sortie=40, latence_ms=250),
        table_tarifs,
        juge,
        VOLUME_RENTABLE,
    )
    seconde = _prouver(
        *base,
        FakeModeleClient(jetons_sortie=40, latence_ms=250),
        table_tarifs,
        juge,
        VOLUME_RENTABLE,
    )

    assert premiere.variante_id == seconde.variante_id
    assert premiere.gain_net_12_mois_usd == seconde.gain_net_12_mois_usd
    assert premiere.economie_mensuelle_usd == seconde.economie_mensuelle_usd
