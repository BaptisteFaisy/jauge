"""Tests des portes déterministes à vérité terrain (piste n° 3, §13.3, §13.4).

Trois choses sont vérifiées :

- chaque porte tranche **sans juge** quand une vérité existe, et se déclare
  **non applicable** (donc non bloquante) quand elle ne peut pas prouver ;
- le verdict est au format des contrôles déterministes, donc un échec de porte
  bloque la proposition de candidat via la condition 3 du §13.4 — sans modifier
  la règle de décision ;
- aucun motif ne recopie le texte de la réponse (ENF-CONF-01).

Les deux tests de la porte code exécutent réellement le bac à sable, sur des
programmes minuscules et sans réseau.
"""

from __future__ import annotations

from jauge.evaluation.bac_a_sable import ParametresBacASable
from jauge.evaluation.checks import ResultatControle
from jauge.evaluation.decision import decider
from jauge.evaluation.verite_terrain import (
    AttenduVerite,
    TypePorte,
    echecs_de_verite,
    evaluer_porte,
    extraire_nombre,
    porte_code,
    porte_numerique,
    porte_schema,
    portes_par_tache,
)
from jauge.models.campaign import ResultatCandidat

CODE_JUSTE = "def triple(x):\n    return x * 3\n"
TEST_JUSTE = "from solution import triple\n\n\ndef test_triple():\n    assert triple(2) == 6\n"
TEST_FAUX = "from solution import triple\n\n\ndef test_triple():\n    assert triple(2) == 7\n"
MARQUEUR_CONFIDENTIEL = "DONNEE-CLIENT-CONFIDENTIELLE"


def candidat(echecs_deterministes: int) -> ResultatCandidat:
    """Candidat par ailleurs conforme aux seuils : seul le compteur d'échecs varie."""
    return ResultatCandidat(
        modele_id="modele-candidat",
        nombre_requetes_rejouees=200,
        taux_equivalence=0.99,
        intervalle_confiance_bas=0.97,
        intervalle_confiance_haut=1.0,
        cout_par_requete_usd=0.002,
        echecs_deterministes=echecs_deterministes,
    )


# --- absence de vérité terrain ------------------------------------------------


def test_sans_attendu_le_juge_reste_seul_juge() -> None:
    """Aucune vérité configurée : la porte est non applicable et ne bloque rien."""
    verdict = evaluer_porte(None, "référence", "réponse candidate")

    assert verdict.ok is True
    assert verdict.critique is False
    assert "juge" in verdict.motif


# --- porte numérique ----------------------------------------------------------


def test_valeur_conforme_a_la_reference() -> None:
    """La réponse porte la référence : la porte tranche sans juge."""
    verdict = porte_numerique("42", AttenduVerite(TypePorte.NUMERIQUE, valeur=42.0))

    assert verdict.ok is True and verdict.nom == "verite_terrain_numerique"


def test_valeur_dans_la_tolerance() -> None:
    """Un écart sous la tolérance relative est accepté (arrondi d'affichage)."""
    attendu = AttenduVerite(TypePorte.NUMERIQUE, valeur=1000.0, tolerance_relative=0.01)

    assert porte_numerique("995", attendu).ok is True
    assert porte_numerique("900", attendu).ok is False


def test_valeur_ecartee_bloque_la_proposition() -> None:
    """Un écart hors tolérance est un échec critique."""
    verdict = porte_numerique("43", AttenduVerite(TypePorte.NUMERIQUE, valeur=42.0))

    assert verdict.ok is False
    assert verdict.critique is True


def test_reponse_sans_valeur_lisible_echoue() -> None:
    """Une réponse qui ne contient aucun nombre ne répond pas à la question."""
    verdict = porte_numerique(
        "je ne sais pas répondre", AttenduVerite(TypePorte.NUMERIQUE, valeur=42.0)
    )

    assert verdict.ok is False and verdict.critique is True
    assert "aucune valeur numérique" in verdict.motif


def test_reference_absente_porte_non_applicable() -> None:
    """Sans valeur de référence, la porte numérique ne prétend rien."""
    verdict = porte_numerique("42", AttenduVerite(TypePorte.NUMERIQUE))

    assert verdict.ok is True and verdict.critique is False


def test_extraction_deterministe_des_nombres() -> None:
    """JSON d'abord (champ connu puis premier numérique), sinon dernier nombre du texte."""
    assert extraire_nombre('{"valeur": 12.5}') == 12.5
    assert extraire_nombre('{"montant_calcule": 3}') == 3.0
    assert extraire_nombre("12") == 12.0
    assert extraire_nombre("3 erreurs trouvées, le total est 42.") == 42.0
    assert extraire_nombre("3,5") == 3.5
    assert extraire_nombre("") is None
    assert extraire_nombre("aucun chiffre ici") is None


def test_booleen_non_pris_pour_un_nombre() -> None:
    """`true` n'est pas 1 : un booléen ne satisfait pas une porte numérique."""
    assert extraire_nombre('{"valeur": true}') is None


# --- porte schéma -------------------------------------------------------------


def test_schema_conforme_valide_sans_juge() -> None:
    """Clés présentes et types identiques à la référence : la porte tranche."""
    reference = '{"label": "facturation", "score": 1}'
    attendu = AttenduVerite(TypePorte.SCHEMA, cles_attendues=("label", "score"))

    verdict = porte_schema(reference, '{"label": "support", "score": 0}', attendu)

    assert verdict.ok is True


def test_cle_manquante_echoue() -> None:
    """Une clé attendue absente est un échec critique."""
    attendu = AttenduVerite(TypePorte.SCHEMA, cles_attendues=("label", "score"))

    verdict = porte_schema('{"label": "x", "score": 1}', '{"label": "x"}', attendu)

    assert verdict.ok is False
    assert "score" in verdict.motif


def test_schema_sans_cles_configurees_non_applicable() -> None:
    """Sans clés attendues, la porte schéma ne tranche pas."""
    verdict = porte_schema('{"a": 1}', '{"b": 2}', AttenduVerite(TypePorte.SCHEMA))

    assert verdict.ok is True and verdict.critique is False


# --- porte code ---------------------------------------------------------------


def test_code_juste_valide_par_execution() -> None:
    """Exécution réelle : les tests de la tâche passent dans le bac à sable."""
    attendu = AttenduVerite(TypePorte.CODE, tests=TEST_JUSTE)

    verdict = porte_code(CODE_JUSTE, attendu)

    assert verdict.ok is True
    assert "bac à sable" in verdict.motif


def test_code_faux_bloque_la_proposition() -> None:
    """Exécution réelle : un code qui échoue aux tests est un échec critique."""
    attendu = AttenduVerite(TypePorte.CODE, tests=TEST_FAUX)

    verdict = porte_code(CODE_JUSTE, attendu)

    assert verdict.ok is False and verdict.critique is True
    assert "test_triple" in verdict.motif


def test_bac_desactive_porte_non_applicable() -> None:
    """Bac désactivé : la porte ne prouve rien, donc elle ne bloque rien."""
    attendu = AttenduVerite(TypePorte.CODE, tests=TEST_JUSTE)

    verdict = porte_code(CODE_JUSTE, attendu, ParametresBacASable(actif=False))

    assert verdict.ok is True and verdict.critique is False
    assert "non exécutable" in verdict.motif


# --- confidentialité des motifs ------------------------------------------------


def test_motif_numerique_sans_texte_de_reponse() -> None:
    """Le motif publié ne recopie ni la réponse ni sa valeur (ENF-CONF-01)."""
    reponse = f"Le montant est de 4300 euros. {MARQUEUR_CONFIDENTIEL}"
    attendu = AttenduVerite(TypePorte.NUMERIQUE, valeur=42.0)

    motif = porte_numerique(reponse, attendu).motif

    assert MARQUEUR_CONFIDENTIEL not in motif
    assert "4300" not in motif and "euros" not in motif


def test_motif_code_sans_source_candidate() -> None:
    """Le motif ne recopie pas le code produit : il nomme les tests en échec."""
    code = f"SECRET_INTERNE = 1\n\n\ndef triple(x):\n    return x * 3  # {MARQUEUR_CONFIDENTIEL}\n"
    attendu = AttenduVerite(TypePorte.CODE, tests=TEST_FAUX)

    motif = porte_code(code, attendu).motif

    assert MARQUEUR_CONFIDENTIEL not in motif
    assert "SECRET_INTERNE" not in motif


# --- intégration à la règle de décision ---------------------------------------


def test_echec_de_porte_bloque_la_decision() -> None:
    """Un échec déterministe empêche de proposer le candidat (§13.4, condition 3)."""
    assert decider(candidat(echecs_deterministes=0)).propose is True

    decision = decider(candidat(echecs_deterministes=1))

    assert decision.propose is False
    assert any("déterministe" in motif for motif in decision.motifs)
    # La condition 3 du §13.4 est celle qui bloque.
    assert [c.nom for c in decision.conditions if not c.remplie] == ["controles_deterministes"]


def test_echecs_de_verite_isolés_des_autres_contrôles() -> None:
    """Le filtre ne retient que les portes à vérité terrain en échec critique."""
    resultats = [
        ResultatControle(nom="verite_terrain_numerique", ok=False, critique=True),
        ResultatControle(nom="longueur_bornee", ok=False, critique=True),
        ResultatControle(nom="verite_terrain", ok=True, critique=False, motif="non applicable"),
    ]

    assert [r.nom for r in echecs_de_verite(resultats)] == ["verite_terrain_numerique"]


def test_aiguillage_des_taches_vers_les_portes() -> None:
    """Chaque tâche sait quelle porte s'applique, ou que le juge reste seul."""
    taches = {
        "generation_code": AttenduVerite(TypePorte.CODE, tests=TEST_JUSTE),
        "calcul": AttenduVerite(TypePorte.NUMERIQUE, valeur=1.0),
        "redaction": None,
    }

    assert portes_par_tache(taches) == {
        "generation_code": "code",
        "calcul": "numerique",
        "redaction": "juge",
    }


def test_la_porte_code_refuse_sans_isolation_noyau(monkeypatch) -> None:
    """Sans isolation noyau, la porte ne s'exécute pas : elle ne prouve rien.

    C'est la différence avec l'ancien comportement : au lieu de faire tourner
    du code hostile dans un simple sous-processus, la porte se déclare non
    applicable et rend la main au juge.
    """
    from jauge.evaluation import bac_a_sable
    from jauge.evaluation.verite_terrain import porte_code

    monkeypatch.setattr(
        bac_a_sable, "niveau_isolation", lambda interpreteur=None: bac_a_sable.ISOLEMENT_GARDE
    )

    verdict = porte_code(CODE_JUSTE, AttenduVerite(TypePorte.CODE, tests=TEST_JUSTE))

    assert verdict.ok is True
    assert verdict.critique is False
    assert "non exécutable" in verdict.motif
