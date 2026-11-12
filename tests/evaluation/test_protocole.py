"""Tests du protocole d'évaluation pré-enregistré et de son scellement (piste n° 1).

Aucun réseau, aucun AWS : le protocole est un artefact déclaratif, scellé par
une empreinte SHA-256 calculée hors ligne.

Ce fichier couvre le protocole lui-même :
- validation des champs engagés (marge, seuils, plan d'échantillonnage) ;
- scellement et **immuabilité** après scellement ;
- empreinte déterministe (indépendante de l'ordre des clés) et sensible au contenu ;
- dérive détectée à la vérification ;
- traçabilité sur la campagne (empreintes de protocole et d'échantillon) ;
- confidentialité : aucun texte de requête n'y figure (ENF-CONF-01).

La règle de décision et la marge de non-infériorité sont testées dans
`tests/evaluation/test_protocole_decision.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as ErreurPydantic

from jauge.common.errors import EtatIncompatibleError, ValidationError
from jauge.evaluation.judge import RegistreCapacite
from jauge.evaluation.protocole import (
    MARGE_NON_INFERIORITE_DEFAUT,
    VERSION_PROTOCOLE,
    PlanEchantillonnage,
    ProtocoleEvaluation,
    SeuilsProtocole,
    construire_protocole,
    controles_deterministes_actifs,
    porter_empreintes,
    resume_pre_enregistrement,
    sceller,
    verifier_protocole_present,
    verifier_scelle,
    versions_harnais_defaut,
)
from jauge.models.campaign import Campagne, StatutCampagne
from jauge.models.pricing import TarifModele
from jauge.models.tenant import Application
from jauge.replay.scellement import EchantillonScelle, empreinte_table_tarifs

MOMENT_FIXE = datetime(2026, 11, 12, 9, 30, tzinfo=UTC)
JUGE = "modele-juge-de-demo"
CANDIDATS = ["candidat-moyen", "candidat-petit"]
TEXTE_SENTINELLE = "SECRET-REQUETE-ne-doit-pas-sortir"


def protocole(app: Application, **champs: object) -> ProtocoleEvaluation:
    """Protocole de référence des tests : celui d'une application de classement."""
    parametres = {
        "application": app,
        "modeles_candidats": CANDIDATS,
        "modele_juge_id": JUGE,
        "controles_deterministes": controles_deterministes_actifs(["classification", "extraction"]),
    }
    parametres.update(champs)
    return construire_protocole(**parametres)  # type: ignore[arg-type]


def forge(scelle: ProtocoleEvaluation, **champs: object) -> ProtocoleEvaluation:
    """Simule un protocole retouché après scellement (contournement par revalidation)."""
    donnees = scelle.model_dump(mode="json")
    donnees.update(champs)
    return ProtocoleEvaluation.model_validate(donnees)


# --- Validation des champs engagés ------------------------------------------


def test_protocole_construit_depuis_l_application(app_classement: Application) -> None:
    """Les seuils viennent de l'application, pas de l'évaluateur (§13.4)."""
    engage = protocole(app_classement)
    assert engage.application_id == app_classement.id
    assert engage.seuils.seuil_equivalence == pytest.approx(app_classement.seuil_qualite)
    assert engage.seuils.borne_basse_intervalle == pytest.approx(
        app_classement.seuil_intervalle_bas
    )
    assert engage.modeles_candidats == sorted(CANDIDATS)
    assert engage.marge_non_inferiorite == pytest.approx(MARGE_NON_INFERIORITE_DEFAUT)
    assert engage.version == VERSION_PROTOCOLE
    assert set(engage.versions_harnais) == set(versions_harnais_defaut())
    assert engage.scelle is None
    assert engage.est_scelle is False


def test_plan_echantillonnage_engage(app_classement: Application) -> None:
    """Taille, graines, strates et exclusions du §13.1 sont figés dans le plan."""
    plan = PlanEchantillonnage(
        taille=200,
        fenetre_jours=14,
        graines=[7, 42],
        strates=["long:classement"],
        exclusions=["sensible"],
    )
    engage = protocole(app_classement, plan=plan)
    assert engage.plan.taille == 200
    assert engage.plan.fenetre_jours == 14
    assert engage.plan.graines == [7, 42]
    assert engage.plan.strates == ["long:classement"]
    assert engage.plan.exclusions == ["sensible"]


def test_marge_negative_refusee() -> None:
    """Une marge de non-infériorité négative n'a pas de sens : refus explicite."""
    with pytest.raises(ErreurPydantic):
        ProtocoleEvaluation(
            application_id="app-demo", modele_juge_id=JUGE, marge_non_inferiorite=-0.01
        )


def test_marge_superieure_a_un_refusee() -> None:
    """Une marge au-delà de 100 % accepterait n'importe quoi : refus explicite."""
    with pytest.raises(ErreurPydantic):
        ProtocoleEvaluation(
            application_id="app-demo", modele_juge_id=JUGE, marge_non_inferiorite=1.5
        )


def test_seuils_hors_intervalle_refuses() -> None:
    """Les seuils doivent rester dans ]0, 1] (§13.4)."""
    for seuil in (0.0, -0.5, 1.5):
        with pytest.raises(ErreurPydantic):
            SeuilsProtocole(seuil_equivalence=seuil)
        with pytest.raises(ErreurPydantic):
            SeuilsProtocole(borne_basse_intervalle=seuil)


def test_plan_hors_bornes_refuse() -> None:
    """La taille d'échantillon reste dans les bornes du socle (10 à 2000)."""
    with pytest.raises(ErreurPydantic):
        PlanEchantillonnage(taille=0)
    with pytest.raises(ErreurPydantic):
        PlanEchantillonnage(taille=5000)


def test_identifiants_vides_refuses() -> None:
    """Un protocole sans application ni juge n'engage personne."""
    with pytest.raises(ErreurPydantic):
        ProtocoleEvaluation(application_id="", modele_juge_id=JUGE)
    with pytest.raises(ErreurPydantic):
        ProtocoleEvaluation(application_id="app-demo", modele_juge_id="")


def test_listes_triees_et_dedupliquees() -> None:
    """L'ordre de saisie ne change pas le protocole : listes triées et uniques."""
    plan = PlanEchantillonnage(strates=["b", "a", "b", "  "], exclusions=["z", "a"])
    assert plan.strates == ["a", "b"]
    assert plan.exclusions == ["a", "z"]
    engage = ProtocoleEvaluation(
        application_id="app-demo",
        modele_juge_id=JUGE,
        modeles_candidats=["m2", "m1", "m2"],
        controles_deterministes=["b", "a", "a"],
    )
    assert engage.modeles_candidats == ["m1", "m2"]
    assert engage.controles_deterministes == ["a", "b"]


def test_controles_actifs_lus_du_registre() -> None:
    """La liste des contrôles vient du registre existant, jamais d'une recopie."""
    noms = controles_deterministes_actifs(["classification"])
    assert "controle_json_valide" in noms
    assert "controle_etiquette_identique" in noms
    assert noms == sorted(noms)


def test_juge_indigne_refuse_au_pre_enregistrement(app_classement: Application) -> None:
    """§13.5 vérifié dès le scellement : le juge doit être au moins aussi capable."""
    registre = RegistreCapacite(
        {app_classement.modele_actuel: 3, JUGE: 1, CANDIDATS[0]: 2, CANDIDATS[1]: 2}
    )
    with pytest.raises(ValidationError):
        protocole(app_classement, registre=registre)


def test_juge_candidat_refuse_au_pre_enregistrement(app_classement: Application) -> None:
    """§13.5 : un juge qui se noterait lui-même est refusé avant tout rejeu."""
    juge = CANDIDATS[0]
    registre = RegistreCapacite({app_classement.modele_actuel: 3, juge: 3, CANDIDATS[1]: 2})
    with pytest.raises(ValidationError):
        protocole(app_classement, modele_juge_id=juge, registre=registre)


# --- Scellement et immuabilité ---------------------------------------------


def test_scellement_pose_empreinte_horodatage_et_auteur(app_classement: Application) -> None:
    """Le sceau dit quoi, quand et par qui (§16)."""
    scelle = sceller(protocole(app_classement), scelle_par="msp-audit", scelle_le=MOMENT_FIXE)
    assert scelle.est_scelle is True
    assert scelle.scelle is not None
    assert scelle.scelle.empreinte == scelle.empreinte
    assert scelle.scelle.scelle_le == MOMENT_FIXE
    assert scelle.scelle.scelle_par == "msp-audit"
    assert len(scelle.scelle.empreinte) == 64
    verifier_scelle(scelle)


def test_protocole_scelle_est_immuable_par_affectation(app_classement: Application) -> None:
    """Toute affectation après scellement lève l'erreur métier explicite."""
    scelle = sceller(protocole(app_classement), scelle_le=MOMENT_FIXE)
    with pytest.raises(EtatIncompatibleError) as erreur:
        scelle.marge_non_inferiorite = 0.9  # type: ignore[misc]
    assert "scellé" in erreur.value.message
    with pytest.raises(EtatIncompatibleError):
        scelle.seuils = SeuilsProtocole(seuil_equivalence=0.6, borne_basse_intervalle=0.5)


def test_protocole_scelle_refuse_modifier_et_copie(app_classement: Application) -> None:
    """Ni `modifier`, ni `model_copy(update=…)` : aucun contournement silencieux."""
    scelle = sceller(protocole(app_classement))
    with pytest.raises(EtatIncompatibleError):
        scelle.modifier(marge_non_inferiorite=0.9)
    with pytest.raises(EtatIncompatibleError):
        scelle.model_copy(update={"marge_non_inferiorite": 0.9})
    with pytest.raises(EtatIncompatibleError):
        sceller(scelle, scelle_le=MOMENT_FIXE)


def test_protocole_non_scelle_reste_modifiable(app_classement: Application) -> None:
    """Avant le scellement, la rédaction du protocole reste possible."""
    brouillon = protocole(app_classement)
    corrige = brouillon.modifier(marge_non_inferiorite=0.05)
    assert corrige.marge_non_inferiorite == pytest.approx(0.05)
    assert brouillon.marge_non_inferiorite == pytest.approx(MARGE_NON_INFERIORITE_DEFAUT)
    brouillon.table_tarifs_empreinte = "0" * 64
    assert brouillon.table_tarifs_empreinte == "0" * 64


# --- Empreinte déterministe -------------------------------------------------


def test_empreinte_independante_de_l_ordre_des_cles(app_classement: Application) -> None:
    """Même protocole, clés saisies dans un autre ordre → même empreinte."""
    premier = protocole(
        app_classement, versions_harnais={"jauge": "0.1.0", "jauge.evaluation.judge": "0.1.0"}
    )
    second = protocole(
        app_classement, versions_harnais={"jauge.evaluation.judge": "0.1.0", "jauge": "0.1.0"}
    )
    assert list(premier.versions_harnais) != list(second.versions_harnais)
    assert premier.empreinte == second.empreinte
    premier_scelle = sceller(premier, scelle_le=MOMENT_FIXE)
    second_scelle = sceller(second, scelle_le=MOMENT_FIXE)
    assert premier_scelle.scelle is not None
    assert second_scelle.scelle is not None
    assert premier_scelle.scelle.empreinte == second_scelle.scelle.empreinte


def test_empreinte_stable_apres_aller_retour_json(app_classement: Application) -> None:
    """Sérialiser puis relire le protocole ne change pas son empreinte."""
    scelle = sceller(protocole(app_classement))
    relu = ProtocoleEvaluation.model_validate_json(scelle.model_dump_json())
    assert relu.empreinte == scelle.empreinte
    verifier_scelle(relu)


def test_empreinte_sensible_a_chaque_engagement(app_classement: Application) -> None:
    """Changer un seul paramètre engagé change l'empreinte."""
    reference = protocole(app_classement)
    variantes = {
        "marge_non_inferiorite": 0.05,
        "modele_juge_id": "autre-juge",
        "prompt_juge_version": "2.0",
        "table_tarifs_empreinte": "a" * 64,
        "controles_deterministes": ["controle_longueur_bornee"],
    }
    for champ, valeur in variantes.items():
        assert protocole(app_classement, **{champ: valeur}).empreinte != reference.empreinte, champ
    # Seuils et plan se varient sur le modèle : la fabrique les lit de l'application.
    for mise_a_jour in (
        {"seuils": SeuilsProtocole(seuil_equivalence=0.97, borne_basse_intervalle=0.9)},
        {"seuils": SeuilsProtocole(seuil_equivalence=0.95, borne_basse_intervalle=0.85)},
        {"plan": PlanEchantillonnage(taille=150)},
        {"plan": PlanEchantillonnage(graines=[7])},
    ):
        variante = reference.model_copy(update=mise_a_jour)
        assert variante.empreinte != reference.empreinte, mise_a_jour


def test_empreinte_de_la_table_tarifs_engagee(
    app_classement: Application, tarifs: list[TarifModele]
) -> None:
    """La table des tarifs fait partie de l'engagement : son empreinte y figure."""
    engage = protocole(app_classement, table_tarifs_empreinte=empreinte_table_tarifs(tarifs))
    modifiee = [tarif.model_copy(update={"prix_sortie_1k": 0.999}) for tarif in tarifs]
    autre = protocole(app_classement, table_tarifs_empreinte=empreinte_table_tarifs(modifiee))
    assert engage.table_tarifs_empreinte == empreinte_table_tarifs(tarifs)
    assert engage.empreinte != autre.empreinte


# --- Dérive détectée --------------------------------------------------------


def test_derive_du_protocole_detectee(app_classement: Application) -> None:
    """Un protocole retouché après scellement ne passe plus la vérification."""
    scelle = sceller(protocole(app_classement))
    falsifie = forge(scelle, marge_non_inferiorite=0.9)
    assert falsifie.scelle is not None
    with pytest.raises(EtatIncompatibleError) as erreur:
        verifier_scelle(falsifie)
    assert erreur.value.details["empreinte_attendue"] == scelle.empreinte
    assert erreur.value.details["empreinte_calculee"] == falsifie.empreinte


def test_seuils_engages_retouches_detectes(app_classement: Application) -> None:
    """Abaisser un seuil après coup est la dérive la plus tentante : elle est refusée."""
    scelle = sceller(protocole(app_classement))
    falsifie = forge(scelle, seuils={"seuil_equivalence": 0.5, "borne_basse_intervalle": 0.4})
    with pytest.raises(EtatIncompatibleError):
        verifier_scelle(falsifie)


def test_protocole_non_scelle_refuse_a_la_verification(app_classement: Application) -> None:
    """Conclure sans engagement préalable est refusé, pas ignoré."""
    with pytest.raises(EtatIncompatibleError):
        verifier_scelle(protocole(app_classement))
    with pytest.raises(EtatIncompatibleError):
        verifier_protocole_present(protocole(app_classement))


def test_protocole_absent_refuse() -> None:
    """Aucun protocole présenté → erreur explicite (jamais un verdict silencieux)."""
    with pytest.raises(EtatIncompatibleError):
        verifier_protocole_present(None)


# --- Traçabilité sur la campagne -------------------------------------------


def campagne(app: Application) -> Campagne:
    """Campagne minimale, sans aucune empreinte de preuve."""
    return Campagne(
        id="c-test-protocole",
        tenant_id=app.tenant_id,
        application_id=app.id,
        modeles_candidats=CANDIDATS,
        modele_juge_id=JUGE,
        cout_estime_usd=1.5,
        statut=StatutCampagne.PLANIFIEE,
    )


def test_campagne_porte_les_deux_empreintes(app_classement: Application) -> None:
    """La campagne cite l'empreinte du protocole et celle de l'échantillon (§16)."""
    scelle = sceller(protocole(app_classement))
    sceau_echantillon = EchantillonScelle(
        empreinte="b" * 64,
        scelle_le=MOMENT_FIXE,
        application_id=app_classement.id,
        graine=7,
        taille=60,
    )
    portee = porter_empreintes(campagne(app_classement), scelle, sceau_echantillon)
    assert scelle.scelle is not None
    assert portee.protocole_empreinte == scelle.scelle.empreinte
    assert portee.echantillon_empreinte == "b" * 64
    assert portee.id == "c-test-protocole"
    assert portee.statut is StatutCampagne.PLANIFIEE


def test_campagne_retrocompatible_sans_empreintes() -> None:
    """Une campagne enregistrée avant ce lot se relit telle quelle (aucun champ requis)."""
    ancienne = Campagne.model_validate(
        {
            "id": "c-ancien",
            "tenant_id": "t-demo-a",
            "application_id": "app-demo",
            "modeles_candidats": ["m1"],
            "cout_estime_usd": 2.0,
        }
    )
    assert ancienne.protocole_empreinte is None
    assert ancienne.echantillon_empreinte is None
    assert porter_empreintes(ancienne) is ancienne


def test_porter_empreintes_refuse_un_protocole_non_scelle(app_classement: Application) -> None:
    """On ne cite pas un engagement qui n'a pas été pris."""
    with pytest.raises(EtatIncompatibleError):
        porter_empreintes(campagne(app_classement), protocole(app_classement))


def test_resume_pre_enregistrement_pour_le_rapport(app_classement: Application) -> None:
    """Vue citable par le rapport et la recommandation, sans aucun texte."""
    scelle = sceller(protocole(app_classement), scelle_par="msp-audit", scelle_le=MOMENT_FIXE)
    portee = porter_empreintes(campagne(app_classement), scelle)
    resume = resume_pre_enregistrement(portee, scelle)
    assert resume["protocole_empreinte"] == scelle.empreinte
    assert resume["marge_non_inferiorite"] == pytest.approx(MARGE_NON_INFERIORITE_DEFAUT)
    assert resume["modele_juge_id"] == JUGE
    assert resume["scelle"] is not None
    assert resume["scelle"]["scelle_par"] == "msp-audit"
    brut = json.dumps(resume, ensure_ascii=False, default=str)
    assert TEXTE_SENTINELLE not in brut


def test_vue_du_protocole_serialisable_et_sans_texte(app_classement: Application) -> None:
    """`vers_dict` passe en JSON et ne transporte aucune donnée de requête."""
    scelle = sceller(protocole(app_classement), scelle_le=MOMENT_FIXE)
    vue = scelle.vers_dict()
    brut = json.dumps(vue, ensure_ascii=False)
    assert json.loads(brut) == vue
    assert TEXTE_SENTINELLE not in brut
    assert vue["scelle"]["algorithme"] == "sha256"
    assert "2026-11-12" in str(vue["scelle"]["scelle_le"])
