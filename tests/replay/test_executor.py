"""Tests de l'exécution d'un rejeu (EF-REJ-02..04, EF-EVA-04, ENF-CONF-01)."""

from __future__ import annotations

import json

import pytest
from tests.replay.constantes import (
    APPLICATION,
    CAMPAGNE,
    DATE_EFFET,
    MODELE_GRAND,
    MODELE_MOYEN,
    MODELE_PETIT,
    REGION_REJEU,
)

from jauge.common.errors import ValidationError
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.models.campaign import ParametresRejeu
from jauge.models.pricing import MetriquesRequete, ServiceTier
from jauge.replay.client import FakeModeleClient
from jauge.replay.executor import ExecuteurRejeu, ResumeRejeu, TextesEnMemoire


def _executeur(client, table_tarifs: TableTarifs) -> ExecuteurRejeu:
    """Exécuteur de test : région et date d'effet de la démonstration."""
    return ExecuteurRejeu(client, table_tarifs, region=REGION_REJEU, a_la_date=DATE_EFFET)


def _rejouer(executeur: ExecuteurRejeu, echantillon, textes, **surcharges):
    """Lance un rejeu sur l'échantillon de démonstration (référence + 2 candidats)."""
    requetes = list(getattr(echantillon, "requetes", echantillon))
    parametres = {
        "campagne_id": CAMPAGNE,
        "application_id": APPLICATION,
        "modele_actuel": MODELE_GRAND,
        "candidats": [MODELE_MOYEN, MODELE_PETIT],
        "echantillon": requetes,
        "parametres": ParametresRejeu(temperature=0.2, max_tokens=512),
        "textes": textes,
        "consigne_systeme": "Tu classes des tickets.",
    }
    parametres.update(surcharges)
    return executeur.rejouer(**parametres)


def test_rejeu_complet_compte_les_executions(
    client_fictif: FakeModeleClient, table_tarifs: TableTarifs, echantillon, textes_source
) -> None:
    """EF-REJ-02 : 2 exécutions de la référence + 1 par candidat, par requête."""
    resultat = _rejouer(_executeur(client_fictif, table_tarifs), echantillon, textes_source)
    taille = echantillon.taille
    assert len(client_fictif.appels) == taille * (2 + 2)
    reference = resultat.resume.pour_modele(MODELE_GRAND)
    candidat = resultat.resume.pour_modele(MODELE_MOYEN)
    assert reference is not None and candidat is not None
    assert reference.nombre_requetes_rejouees == taille
    assert reference.executions == taille * 2
    assert candidat.nombre_requetes_rejouees == taille
    assert candidat.executions == taille


def test_cout_par_requete_recalcule_par_le_code(
    client_fictif: FakeModeleClient, table_tarifs: TableTarifs, echantillon, textes_source
) -> None:
    """EF-REJ-04 : le coût vient de `calculer_cout_requete` au tarif Flex, pas d'un LLM."""
    resultat = _rejouer(_executeur(client_fictif, table_tarifs), echantillon, textes_source)
    petit = resultat.resume.pour_modele(MODELE_PETIT)
    assert petit is not None
    tarif = table_tarifs.trouver(MODELE_PETIT, REGION_REJEU, ServiceTier.FLEX, DATE_EFFET)
    attendu = calculer_cout_requete(
        tarif,
        MetriquesRequete(
            jetons_entree=petit.jetons_entree_total,
            jetons_sortie=petit.jetons_sortie_total,
        ),
    ).cout_total
    assert petit.cout_total_usd == pytest.approx(attendu, rel=1e-6)
    assert petit.cout_par_requete_usd == pytest.approx(attendu / echantillon.taille, rel=1e-6)


def test_le_petit_modele_coute_moins_que_le_grand(
    client_fictif: FakeModeleClient, table_tarifs: TableTarifs, echantillon, textes_source
) -> None:
    """La hiérarchie des coûts suit la table des tarifs (Annexe D)."""
    resultat = _rejouer(_executeur(client_fictif, table_tarifs), echantillon, textes_source)
    grand = resultat.resume.pour_modele(MODELE_GRAND)
    petit = resultat.resume.pour_modele(MODELE_PETIT)
    assert grand is not None and petit is not None
    assert petit.cout_par_requete_usd < grand.cout_par_requete_usd


def test_latences_percentiles(table_tarifs: TableTarifs, echantillon, textes_source) -> None:
    """EF-REJ-04 : p50 et p95 viennent de `jauge.engine.stats.percentile`."""
    client = FakeModeleClient(latence_ms=400)
    resultat = _rejouer(_executeur(client, table_tarifs), echantillon, textes_source)
    reference = resultat.resume.pour_modele(MODELE_GRAND)
    assert reference is not None
    assert reference.latence_p50_ms == 400
    assert reference.latence_p95_ms == 400


def test_auto_accord_par_defaut(
    client_fictif: FakeModeleClient, table_tarifs: TableTarifs, echantillon, textes_source
) -> None:
    """EF-EVA-04 : sans variabilité, le modèle actuel est d'accord avec lui-même à 100 %."""
    resultat = _rejouer(_executeur(client_fictif, table_tarifs), echantillon, textes_source)
    assert resultat.resume.auto_accord_modele_actuel == 1.0


def test_auto_accord_avec_variabilite(
    table_tarifs: TableTarifs, echantillon, textes_source
) -> None:
    """Une variabilité simulée fait baisser l'auto-accord du modèle actuel."""
    client = FakeModeleClient(variabilite=True)
    resultat = _rejouer(_executeur(client, table_tarifs), echantillon, textes_source)
    assert resultat.resume.auto_accord_modele_actuel < 1.0


def test_niveau_flex_applique_a_toutes_les_demandes(
    client_fictif: FakeModeleClient, table_tarifs: TableTarifs, echantillon, textes_source
) -> None:
    """EF-REJ-03 : chaque invocation part au niveau Flex, jamais en Standard."""
    _rejouer(_executeur(client_fictif, table_tarifs), echantillon, textes_source)
    assert {demande.niveau for demande in client_fictif.appels} == {ServiceTier.FLEX}


def test_parametres_identiques_entre_reference_et_candidats(
    client_fictif: FakeModeleClient, table_tarifs: TableTarifs, echantillon, textes_source
) -> None:
    """EF-REJ-02 : la référence et les candidats reçoivent exactement les mêmes paramètres."""
    _rejouer(_executeur(client_fictif, table_tarifs), echantillon, textes_source)
    demandes = client_fictif.demandes_pour(echantillon.identifiants[0])
    assert len(demandes) == 4
    assert all(demande.parametres == demandes[0].parametres for demande in demandes)
    assert len({demande.texte_requete for demande in demandes}) == 1
    assert len({demande.consigne_systeme for demande in demandes}) == 1


def test_paires_completes_dans_le_plan_de_donnees(
    client_fictif: FakeModeleClient, table_tarifs: TableTarifs, echantillon, textes_source
) -> None:
    """Une paire rejouée par couple (requête, candidat), avec les deux textes."""
    resultat = _rejouer(_executeur(client_fictif, table_tarifs), echantillon, textes_source)
    assert len(resultat.paires) == echantillon.taille * 2
    paire = resultat.paires[0]
    assert paire.texte_requete.startswith("Question de demonstration")
    assert paire.reponse_reference and paire.reponse_candidate
    assert paire.campagne_id == CAMPAGNE


def test_resume_sans_aucun_texte(
    client_fictif: FakeModeleClient, table_tarifs: TableTarifs, echantillon, textes_source
) -> None:
    """ENF-CONF-01 : le résumé de contrôle ne contient aucun texte de requête ni de réponse."""
    resultat = _rejouer(_executeur(client_fictif, table_tarifs), echantillon, textes_source)
    serialise = json.dumps(resultat.vers_controle().__dict__, default=str)
    assert "Question de demonstration" not in serialise
    assert "reponse-demo-grand-modele" not in serialise
    assert "texte_requete" not in serialise


def test_candidat_en_erreur_ne_bloque_pas_le_rejeu(
    table_tarifs: TableTarifs, echantillon, textes_source
) -> None:
    """Une panne de modèle est comptée, le reste du rejeu continue (reprise)."""
    client = FakeModeleClient(modeles_en_erreur={MODELE_MOYEN})
    resultat = _rejouer(_executeur(client, table_tarifs), echantillon, textes_source)
    moyen = resultat.resume.pour_modele(MODELE_MOYEN)
    assert moyen is not None
    assert moyen.erreurs == echantillon.taille
    assert moyen.executions == 0
    assert resultat.resume.pour_modele(MODELE_PETIT).executions == echantillon.taille
    assert len(resultat.paires) == echantillon.taille


def test_echantillon_vide_refuse(table_tarifs: TableTarifs, textes_source) -> None:
    """Un rejeu sans requête échoue explicitement avant tout appel de modèle."""
    with pytest.raises(ValidationError):
        _rejouer(_executeur(FakeModeleClient(), table_tarifs), [], textes_source)


def test_texte_manquant_refuse(table_tarifs: TableTarifs, echantillon) -> None:
    """Un texte absent du plan de données est une erreur métier, pas un KeyError."""
    with pytest.raises(ValidationError):
        _rejouer(_executeur(FakeModeleClient(), table_tarifs), echantillon, TextesEnMemoire({}))


def test_resume_est_immuable_et_typé(
    client_fictif: FakeModeleClient, table_tarifs: TableTarifs, echantillon, textes_source
) -> None:
    """Le résumé rendu est bien un `ResumeRejeu` et reste lisible sans le rejeu complet."""
    resultat = _rejouer(_executeur(client_fictif, table_tarifs), echantillon, textes_source)
    assert isinstance(resultat.vers_controle(), ResumeRejeu)
    assert resultat.resume.taille_echantillon == echantillon.taille
    assert resultat.resume.niveau == ServiceTier.FLEX


def test_reference_partiellement_en_erreur(
    table_tarifs: TableTarifs, echantillon, textes_source
) -> None:
    """Si une seule des deux exécutions de référence aboutit, la requête est écartée."""

    class _ClientFragile:
        """Échoue une invocation sur deux, quel que soit le modèle."""

        def __init__(self) -> None:
            self.appels = 0

        def invoquer(self, demande):
            from jauge.common.errors import ServiceIndisponibleError
            from jauge.replay.client import ResultatInvocation

            self.appels += 1
            if self.appels % 2 == 0:
                raise ServiceIndisponibleError("panne simulee", modele=demande.modele_id)
            return ResultatInvocation(
                modele_id=demande.modele_id,
                texte="ok",
                jetons_entree=10,
                jetons_sortie=5,
                latence_ms=100,
                niveau=demande.niveau,
            )

    resultat = _rejouer(_executeur(_ClientFragile(), table_tarifs), echantillon, textes_source)
    reference = resultat.resume.pour_modele(MODELE_GRAND)
    assert reference is not None
    assert reference.erreurs == echantillon.taille
    assert resultat.resume.auto_accord_modele_actuel == 0.0
