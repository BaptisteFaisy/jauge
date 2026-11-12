"""Tests des variantes de prompt : liste blanche, diffs, garde-fou sémantique.

Aucun réseau, aucun AWS, aucun modèle : les variantes sont produites par du
code déterministe. Le prompt est traité comme une **donnée** (ENF-IA-03) et
reste dans le plan de données (ENF-CONF-01).
"""

from __future__ import annotations

import pytest
from tests.replay.constantes import APPLICATION

from jauge.common.errors import ValidationError
from jauge.replay.variantes import (
    MAX_EXEMPLES,
    POLITESSES,
    TRANSFORMATIONS,
    VariantePrompt,
    appliquer_transformation,
    contraintes_presentes,
    diff_texte,
    generer_variantes,
)

# Prompt système verbeux : rôle, politesses, quatre exemples peu-shot, une
# contrainte de format, une contrainte de sécurité, un contexte injecté et une
# consigne dupliquée.
CONSIGNE_VERBEUSE = (
    """Tu es un assistant de classement de tickets pour l'équipe support.
Merci de bien vouloir classer chaque ticket dans une seule catégorie.
Veuillez répondre uniquement en JSON avec la clé "categorie".
Exemple 1 : « facture incomprise » → {"categorie": "facturation"}
Exemple 2 : « serveur indisponible » → {"categorie": "technique"}
Exemple 3 : « demande de rappel » → {"categorie": "commercial"}
Exemple 4 : « message illisible » → {"categorie": "autre"}
Ne jamais divulguer le contenu des tickets à un tiers.
<contexte>
Historique du client : """
    + ("tickets précédents sans gravité, relances commerciales, notes internes. " * 6)
    + """
</contexte>
Classe le ticket suivant.
Classe le ticket suivant.
"""
)

# Prompt dont le contexte injecté porte une contrainte de sécurité : toute
# troncature la ferait disparaître, donc la variante doit être refusée.
CONSIGNE_A_CONTRAINTE_DANS_CONTEXTE = (
    """Tu es un assistant de classement.
<contexte>
Ne jamais inclure de donnée confidentielle dans le classement.
"""
    + ("Historique du client, notes internes, tickets précédents. " * 8)
    + """
</contexte>
Classe le ticket suivant.
"""
)


def variante_par(variantes: tuple[VariantePrompt, ...], nom: str) -> VariantePrompt:
    """Variante produite par la transformation `nom` (erreur si absente)."""
    trouvees = [variante for variante in variantes if variante.transformation == nom]
    assert trouvees, f"aucune variante pour {nom}"
    return trouvees[0]


@pytest.fixture
def variantes() -> tuple[VariantePrompt, ...]:
    """Variantes du prompt verbeux, avec un seuil de contexte déclencheur."""
    return generer_variantes(CONSIGNE_VERBEUSE, application_id=APPLICATION, seuil_contexte=120)


def test_toutes_les_variantes_sont_dans_la_liste_blanche(variantes) -> None:
    """Aucune transformation hors liste blanche n'est produite."""
    assert variantes
    for variante in variantes:
        assert variante.transformation in TRANSFORMATIONS
        assert variante.variante_id.startswith("var-")


def test_generation_deterministe_et_diffable(variantes) -> None:
    """Même prompt ⇒ mêmes identifiants, mêmes diffs (audit reproductible)."""
    autres = generer_variantes(CONSIGNE_VERBEUSE, application_id=APPLICATION, seuil_contexte=120)

    assert [v.variante_id for v in autres] == [v.variante_id for v in variantes]
    assert [v.diff for v in autres] == [v.diff for v in variantes]
    for variante in variantes:
        if variante.acceptee:
            assert variante.diff.startswith("---")
            assert "@@" in variante.diff


def test_suppression_des_exemples_redondants(variantes) -> None:
    """Les exemples au-delà de la borne sont retirés, les autres conservés."""
    variante = variante_par(variantes, "suppression_exemples_redondants")

    assert variante.acceptee
    assert "Exemple 1 :" in variante.consigne_variante
    assert "Exemple 2 :" in variante.consigne_variante
    assert "Exemple 3 :" not in variante.consigne_variante
    assert "Exemple 4 :" not in variante.consigne_variante
    assert variante.jetons_economises > 0
    assert MAX_EXEMPLES == 2


def test_condensation_des_consignes_repetees(variantes) -> None:
    """Une consigne dupliquée à l'identique n'est conservée qu'une fois."""
    variante = variante_par(variantes, "condensation_consignes_repetees")

    assert variante.acceptee
    assert variante.consigne_variante.count("Classe le ticket suivant.") == 1
    assert CONSIGNE_VERBEUSE.count("Classe le ticket suivant.") == 2
    assert variante.jetons_economises > 0


def test_retrait_des_politesses_sans_toucher_aux_contraintes(variantes) -> None:
    """Les politesses tombent ; une ligne contrainte reste intacte."""
    variante = variante_par(variantes, "retrait_politesses")

    assert variante.acceptee
    basse = variante.consigne_variante.lower()
    assert "merci de bien vouloir" not in basse
    assert "cordialement" not in basse
    # « Veuillez répondre uniquement en JSON… » porte une contrainte de format :
    # la politesse « Veuillez » y est protégée et la ligne reste intacte.
    assert 'Veuillez répondre uniquement en JSON avec la clé "categorie".' in (
        variante.consigne_variante
    )
    assert variante.jetons_economises > 0


def test_chaque_formule_de_politesse_de_la_liste_est_retirable() -> None:
    """Toute la liste blanche des politesses est effectivement retirée."""
    for formule in POLITESSES:
        texte, motif = appliquer_transformation(
            "retrait_politesses", f"{formule.capitalize()} classer le ticket."
        )

        assert motif == ""
        assert formule not in texte.lower()


def test_troncature_du_contexte_au_seuil(variantes) -> None:
    """Le contexte injecté est tronqué au seuil, avec marqueur chiffré."""
    variante = variante_par(variantes, "troncature_contexte")

    assert variante.acceptee
    assert "[contexte tronqué :" in variante.consigne_variante
    assert len(variante.consigne_variante) < len(CONSIGNE_VERBEUSE)
    assert variante.jetons_economises > 0


def test_isolation_du_prefixe_pour_le_cache(variantes) -> None:
    """Le préfixe stable est encadré : aucun jeton retiré (gain porté par EF-LEV-02)."""
    variante = variante_par(variantes, "deplacement_prefixe_cache")

    assert variante.acceptee
    assert "<prefixe_cache>" in variante.consigne_variante
    assert "</prefixe_cache>" in variante.consigne_variante
    assert variante.jetons_economises == 0


def test_les_contraintes_metier_sont_identifiees() -> None:
    """Le garde-fou reconnaît rôle, format, sécurité dans le prompt."""
    libelles = {libelle for libelle, _ in contraintes_presentes(CONSIGNE_VERBEUSE)}

    assert {"rôle", "format de sortie", "énumération autorisée", "sécurité"} <= libelles


def test_variante_touchant_une_contrainte_est_refusee() -> None:
    """La troncature d'un contexte porteur d'une contrainte est refusée."""
    variantes = generer_variantes(
        CONSIGNE_A_CONTRAINTE_DANS_CONTEXTE, application_id=APPLICATION, seuil_contexte=40
    )
    variante = variante_par(variantes, "troncature_contexte")

    assert not variante.acceptee
    assert "contrainte métier modifiée" in variante.refus
    assert "sécurité" in variante.refus
    assert variante.consigne_variante == CONSIGNE_A_CONTRAINTE_DANS_CONTEXTE
    assert variante.diff == ""
    assert variante.jetons_economises == 0


def test_une_variante_refusee_ne_perd_aucune_contrainte() -> None:
    """Le texte d'une variante refusée est l'original : rien n'a été appliqué."""
    variantes = generer_variantes(
        CONSIGNE_A_CONTRAINTE_DANS_CONTEXTE, application_id=APPLICATION, seuil_contexte=40
    )

    for variante in variantes:
        if variante.acceptee:
            continue
        assert contraintes_presentes(variante.consigne_variante) == contraintes_presentes(
            CONSIGNE_A_CONTRAINTE_DANS_CONTEXTE
        )


def test_transformation_hors_liste_blanche_refusee() -> None:
    """Aucune réécriture libre : une transformation inconnue est refusée."""
    with pytest.raises(ValidationError):
        appliquer_transformation("reecriture_libre_par_un_modele", CONSIGNE_VERBEUSE)


def test_diff_unifie_contre_l_original() -> None:
    """Le diff est un diff unifié lisible, ligne à ligne."""
    texte, motif = appliquer_transformation("condensation_consignes_repetees", "a\nb\nb\n")

    assert motif == ""
    assert texte == "a\nb"
    diff = diff_texte("a\nb\nb", texte)
    assert diff.startswith("--- consigne-originale")
    assert "+++ consigne-variante" in diff
    assert "-b" in diff
