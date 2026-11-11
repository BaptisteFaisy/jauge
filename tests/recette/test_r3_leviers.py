"""Recette R3 — application de classement sur un grand modèle (§22, Annexe A).

Scénario : « Classement sur un grand modèle » → le levier **modèle
surdimensionné** doit être détecté **avec une économie estimée** (EF-LEV-01,
EF-LEV-06).

Le test exerce le détecteur réel du module M3 (`jauge.levers.detecter_leviers`)
sur un profil agrégé calculé depuis le trafic synthétique de la démonstration
(`demo/traffic.py`, application `ticket-classifier` servie par le grand modèle
alors qu'elle fait de la classification JSON courte — §18.1). Le levier détecté
est ensuite publié sur l'API du plan de contrôle (`GET /v1/applications/{id}/levers`).

Aucun texte de requête n'entre dans le profil (ENF-CONF-01) : uniquement des
longueurs, des compteurs et des empreintes. Aucun réseau.
"""

from __future__ import annotations

import json

from demo.profils import DATE_FIN_DEFAUT, GRAINE_DEFAUT, PROFILS
from demo.seed import construire_agregats
from demo.socle import SocleDemo, charger_socle_demo
from demo.traffic import JournalInvocation, generer_trafic
from fastapi.testclient import TestClient
from tests.conftest import GRAND_MODELE, PETIT_MODELE, REGION
from tests.recette.conftest import entetes

from jauge.engine.calculator import TableTarifs
from jauge.engine.stats import percentile
from jauge.levers import ContexteLevier, ParametresLevier, ProfilRequetes, detecter_leviers
from jauge.models.cost import AgregatQuotidien
from jauge.models.lever import Levier, TypeLevier
from jauge.models.tenant import Tenant
from jauge.store.memory import MemoryStore

APP_CLASSEMENT = "ticket-classifier"
JOURS_OBSERVES = 3


def _texte_sortie(record: JournalInvocation) -> str:
    """Texte de la réponse du modèle, extrait de l'enveloppe Converse (plan de données)."""
    if not record.corps_sortie:
        return ""
    try:
        enveloppe = json.loads(record.corps_sortie)
        contenu = enveloppe["output"]["message"]["content"]
        return contenu[0].get("text", "") if contenu else ""
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return ""


def _est_structuree(texte: str) -> bool:
    """Une sortie est « structurée » si c'est un objet JSON valide (Annexe A)."""
    try:
        return isinstance(json.loads(texte), dict)
    except json.JSONDecodeError:
        return False


def profil_agrege(records: list[JournalInvocation]) -> ProfilRequetes:
    """Profil agrégé de l'application, calculé depuis les journaux (jamais de texte)."""
    profil_demo = PROFILS[APP_CLASSEMENT]
    reussies = [r for r in records if r.code_erreur is None]
    entrees = [r.jetons_entree for r in reussies]
    sorties = [r.jetons_sortie for r in reussies]
    textes = [_texte_sortie(r) for r in reussies]
    structurees = sum(1 for texte in textes if _est_structuree(texte))
    signatures = {r.signature_requete() for r in reussies}
    heures = [0] * 24
    for record in reussies:
        heures[record.event_time.hour] += 1
    return ProfilRequetes(
        longueur_entree_moyenne=sum(entrees) / len(entrees),
        longueur_entree_p95=int(percentile(entrees, 95)),
        longueur_sortie_moyenne=sum(sorties) / len(sorties),
        longueur_sortie_mediane=int(percentile(sorties, 50)),
        longueur_sortie_p95=int(percentile(sorties, 95)),
        limite_sortie_modele=profil_demo.max_tokens,
        requetes_par_type_tache={profil_demo.type_requete: len(reussies)},
        part_sortie_structuree=structurees / len(reussies),
        diversite_sorties=len(set(textes)) / len(reussies),
        requetes_par_heure=tuple(heures),
        nombre_erreurs=len(records) - len(reussies),
        requetes_par_niveau={profil_demo.niveau.value: len(records)},
        part_requetes_identiques=1.0 - len(signatures) / len(reussies),
    )


def contexte_classement(
    socle: SocleDemo, table_socle: TableTarifs
) -> tuple[ContexteLevier, list[JournalInvocation]]:
    """Contexte de détection du classement de tickets, bâti sur le trafic de démo."""
    records = generer_trafic(graine=GRAINE_DEFAUT, jours=JOURS_OBSERVES, date_fin=DATE_FIN_DEFAUT)
    du_classement = [r for r in records if r.application_id == APP_CLASSEMENT]
    agregats: tuple[AgregatQuotidien, ...] = tuple(
        a
        for a in construire_agregats(records, socle, table_socle)
        if a.application_id == APP_CLASSEMENT
    )
    tenant: Tenant = socle.tenant_a
    contexte = ContexteLevier(
        tenant_id=tenant.id,
        application_id=APP_CLASSEMENT,
        modele_actuel=GRAND_MODELE,
        profil=profil_agrege(du_classement),
        parametres=ParametresLevier(date_reference=DATE_FIN_DEFAUT, region=REGION),
        agregats=agregats,
        modeles_autorises=tuple(tenant.modeles_autorises),
        table_tarifs=table_socle,
    )
    return contexte, du_classement


def levier_surdimensionne(contexte: ContexteLevier) -> Levier:
    """Levier « modèle surdimensionné » produit par le détecteur M3 réel."""
    leviers = detecter_leviers(contexte)
    trouves = [
        levier for levier in leviers if levier.type_levier is TypeLevier.MODELE_SURDIMENSIONNE
    ]
    assert len(trouves) == 1, f"leviers détectés : {[lev.type_levier.value for lev in leviers]}"
    return trouves[0]


def test_r3_levier_modele_surdimensionne_detecte_avec_economie(
    store_recette: MemoryStore, table_socle: TableTarifs
) -> None:
    """R3 : le détecteur M3 trouve « modèle surdimensionné » avec une économie estimée."""
    socle = charger_socle_demo()
    store_recette.enregistrer_tenant(socle.tenant_a)
    store_recette.enregistrer_application(socle.applications[APP_CLASSEMENT])

    contexte, du_classement = contexte_classement(socle, table_socle)
    assert len(du_classement) > 500, "volume de classement insuffisant pour la recette"

    # Le signal de l'Annexe A est bien présent dans le trafic de démonstration :
    # requêtes courtes, sorties courtes et structurées, tâche de classification.
    assert contexte.profil.longueur_entree_moyenne < 512
    assert contexte.profil.longueur_sortie_moyenne < 256
    assert contexte.profil.part_sortie_structuree > 0.9

    levier = levier_surdimensionne(contexte)

    # R3 : le levier est détecté, AVEC une économie estimée (EF-LEV-01/06).
    assert levier.economie_mensuelle_estimee_usd > 0.0
    assert levier.application_id == APP_CLASSEMENT
    assert levier.tenant_id == socle.tenant_a.id
    assert levier.action_proposee == "Rejeu sur des modèles plus petits"
    assert levier.preuve_exigee == "Campagne de rejeu"
    # Le modèle autorisé le moins cher est proposé comme candidat d'estimation.
    assert levier.candidats_suggeres[0] == PETIT_MODELE
    # Les indices citent les signaux de l'Annexe A (tâche simple, volumes courts).
    assert any("Tâche simple" in indice for indice in levier.indices)
    assert levier.confiance > 0.0

    # Le levier est enregistrable dans le store cloisonné du plan de contrôle.
    store_recette.enregistrer_levier(levier)
    assert store_recette.lister_leviers(socle.tenant_a.id, APP_CLASSEMENT) == [levier]


def test_r3_levier_publie_sur_l_api(
    client_api: TestClient, store_recette: MemoryStore, table_socle: TableTarifs
) -> None:
    """R3 (complément) : le levier est exposé, classé, par `GET /v1/applications/{id}/levers`."""
    socle = charger_socle_demo()
    store_recette.enregistrer_tenant(socle.tenant_a)
    store_recette.enregistrer_application(socle.applications[APP_CLASSEMENT])

    contexte, _ = contexte_classement(socle, table_socle)
    for levier in detecter_leviers(contexte):
        store_recette.enregistrer_levier(levier)

    reponse = client_api.get(
        f"/v1/applications/{APP_CLASSEMENT}/levers", headers=entetes(socle.tenant_a.id)
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    types = [levier["type_levier"] for levier in corps]
    assert TypeLevier.MODELE_SURDIMENSIONNE.value in types
    surdimensionne = next(
        levier
        for levier in corps
        if levier["type_levier"] == TypeLevier.MODELE_SURDIMENSIONNE.value
    )
    assert surdimensionne["economie_mensuelle_estimee_usd"] > 0.0
    # EF-LEV-06 : les leviers sont classés par économie décroissante.
    economies = [levier["economie_mensuelle_estimee_usd"] for levier in corps]
    assert economies == sorted(economies, reverse=True)


def test_r3_detection_reproductible(table_socle: TableTarifs) -> None:
    """R3 (robustesse) : même graine ⇒ même économie estimée (§13.1, tirage déterministe)."""
    socle = charger_socle_demo()
    contexte, _ = contexte_classement(socle, table_socle)
    premier = levier_surdimensionne(contexte).economie_mensuelle_estimee_usd
    second_contexte, _ = contexte_classement(socle, table_socle)
    second = levier_surdimensionne(second_contexte).economie_mensuelle_estimee_usd
    assert premier == second
