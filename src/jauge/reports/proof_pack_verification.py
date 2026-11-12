"""Rejeu des vérifications d'un dossier de preuve, depuis le pack seul.

C'est le cœur de la piste 6 : un tiers ne doit pas avoir à faire confiance. Il
reçoit le pack, et ce module **recalcule** tout ce qui est recalculable, puis
compare aux valeurs déclarées :

- la **chaîne de hachage** est refaite section par section ;
- les **intervalles de Wilson** sont refaits à partir des effectifs (succès et n)
  avec `jauge.engine.stats.intervalle_wilson` ;
- le **coût par requête** est refait à partir du profil moyen de jetons et de la
  **table des tarifs scellée dans le pack** (formule de l'Annexe D, via
  `jauge.engine.calculator`) ;
- l'**empreinte de l'échantillon** est refaite depuis les identifiants listés.

Les cohérences **entre** sections (économie, part du MSP, recommandation Annexe B,
accord juge/humain, contrôles déterministes, table des tarifs) sont vérifiées par
`jauge.reports.proof_pack_coherence`.

Aucune valeur n'est recopiée du pack sans être confrontée à un calcul, et aucun
appel réseau n'est fait : le vérificateur travaille sur le document JSON, ce qui
permet de vérifier un pack archivé, reçu par courriel ou téléchargé, sans accès à
Jauge ni à AWS.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from jauge.common.errors import TarifInconnuError
from jauge.engine.calculator import TableTarifs
from jauge.engine.stats import intervalle_wilson
from jauge.models.pricing import ServiceTier
from jauge.reports.proof_pack_coherence import (
    verifier_controles,
    verifier_economie,
    verifier_recommandation,
    verifier_relecture,
    verifier_tarifs,
)
from jauge.reports.proof_pack_entrees import CHAMPS_PROFIL_JETONS
from jauge.reports.proof_pack_sections import cout_recalcule, table_depuis_lignes
from jauge.reports.proof_pack_verif_base import (
    TOLERANCE_COUT,
    TOLERANCE_TAUX,
    ResultatRejeu,
    Verification,
    candidats,
    egal,
    sections_de,
)
from jauge.reports.scellement import ResultatChaine, empreinte, verifier_chaine

# Ré-exports : les appelants (tests, API, portail) n'ont qu'un module à importer.
__all__ = [
    "TOLERANCE_COUT",
    "TOLERANCE_TAUX",
    "ResultatRejeu",
    "Verification",
    "rejouer_verifications",
]


def rejouer_verifications(pack: Any) -> ResultatRejeu:
    """Recalcule tout ce qui peut l'être depuis le pack, et compare aux déclarations.

    Le pack est la **seule** entrée : ni le store, ni la campagne, ni la
    recommandation ne sont relus.
    """
    sections = sections_de(pack)
    verifications: list[Verification] = [_verifier_chaine(pack)]
    verifications += _verifier_echantillon(sections)
    verifications += _verifier_resultats(sections)
    verifications += _verifier_couts(sections)
    verifications += verifier_controles(sections)
    verifications += verifier_economie(sections)
    verifications += verifier_recommandation(sections)
    verifications += verifier_relecture(sections)
    verifications += verifier_tarifs(sections)
    return ResultatRejeu(tuple(verifications))


def _verifier_chaine(pack: Any) -> Verification:
    """La chaîne de hachage : chaque section est-elle encore celle qui a été scellée ?"""
    resultat: ResultatChaine = verifier_chaine(pack)
    detail = (
        "chaîne conforme : chaque section est scellée et l'ordre des maillons est respecté"
        if resultat.conforme
        else f"rupture à la position {resultat.position} ({resultat.section}) : {resultat.raison}"
    )
    return Verification(
        nom="chaine_hachage",
        section="scellement",
        conforme=resultat.conforme,
        attendu=resultat.empreinte_attendue or None,
        declare=resultat.empreinte_calculee or None,
        detail=detail,
    )


def _verifier_echantillon(sections: Mapping[str, Any]) -> list[Verification]:
    """L'échantillon figé : taille déclarée et empreinte globale (§13.1)."""
    echantillon = sections.get("echantillon", {})
    lignes = echantillon.get("requetes", [])
    empreinte_calculee = empreinte(lignes)
    return [
        Verification(
            nom="echantillon.taille",
            section="echantillon",
            conforme=echantillon.get("taille") == len(lignes),
            attendu=len(lignes),
            declare=echantillon.get("taille"),
            detail="la taille déclarée doit égaler le nombre de requêtes listées",
        ),
        Verification(
            nom="echantillon.empreinte",
            section="echantillon",
            conforme=egal(echantillon.get("empreinte_echantillon"), empreinte_calculee),
            attendu=empreinte_calculee,
            declare=echantillon.get("empreinte_echantillon"),
            detail="l'empreinte de l'échantillon se recalcule depuis les identifiants listés",
        ),
    ]


def _verifier_resultats(sections: Mapping[str, Any]) -> list[Verification]:
    """Intervalles de Wilson refaits depuis les effectifs de chaque candidat."""
    verifications: list[Verification] = []
    for candidat in candidats(sections):
        modele = candidat.get("modele_id", "?")
        succes = candidat.get("succes")
        effectif = candidat.get("nombre_requetes_rejouees")
        # Un pack falsifié peut déclarer n'importe quoi : le vérificateur ne doit
        # jamais planter dessus (une proportion hors de [0, 1] casse Wilson).
        effectifs_ok = (
            isinstance(succes, int)
            and isinstance(effectif, int)
            and effectif > 0
            and 0 <= succes <= effectif
        )
        verifications.append(
            Verification(
                nom=f"candidat.{modele}.effectifs",
                section="resultats",
                conforme=effectifs_ok,
                attendu=f"0 <= succes <= {effectif}",
                declare=succes,
                detail="les effectifs déclarés doivent être cohérents",
            )
        )
        if not effectifs_ok:
            continue
        intervalle = intervalle_wilson(succes, effectif)
        for cle, valeur in (
            ("taux_equivalence", intervalle.taux),
            ("intervalle_confiance_bas", intervalle.bas),
            ("intervalle_confiance_haut", intervalle.haut),
        ):
            declaree = candidat.get(cle)
            verifications.append(
                Verification(
                    nom=f"candidat.{modele}.{cle}",
                    section="resultats",
                    conforme=egal(declaree, valeur, TOLERANCE_TAUX),
                    attendu=valeur,
                    declare=declaree,
                    detail="intervalle de Wilson recalculé depuis les effectifs (succès / n)",
                )
            )
    return verifications


def _contexte_tarifs(sections: Mapping[str, Any]) -> tuple[TableTarifs, str, ServiceTier, date]:
    """Table des tarifs, région, niveau et date, relus depuis la section protocole."""
    tarifs = sections.get("protocole", {}).get("tarifs", {})
    table = table_depuis_lignes(tarifs.get("lignes", []))
    region = str(tarifs.get("region", ""))
    niveau = ServiceTier(str(tarifs.get("niveau", ServiceTier.STANDARD.value)))
    a_la_date = date.fromisoformat(str(tarifs.get("date_reference")))
    return table, region, niveau, a_la_date


def _verifier_couts(sections: Mapping[str, Any]) -> list[Verification]:
    """Coût par requête recalculé (Annexe D) sur la table des tarifs scellée."""
    verifications: list[Verification] = []
    if not sections.get("protocole", {}).get("tarifs", {}).get("lignes"):
        return verifications
    try:
        table, region, niveau, a_la_date = _contexte_tarifs(sections)
    except (KeyError, TypeError, ValueError) as erreur:
        return [
            Verification(
                nom="tarifs.lisibles",
                section="protocole",
                conforme=False,
                detail=f"table des tarifs illisible dans le pack : {erreur}",
            )
        ]
    for candidat in candidats(sections):
        if candidat.get("cout_recalculable"):
            verifications.append(
                _verifier_cout_candidat(candidat, table, region, niveau, a_la_date)
            )
    return verifications


def _verifier_cout_candidat(
    candidat: Mapping[str, Any],
    table: TableTarifs,
    region: str,
    niveau: ServiceTier,
    a_la_date: date,
) -> Verification:
    """Recalcule le coût par requête d'un candidat (Annexe D) et le compare au déclaré."""
    modele = candidat.get("modele_id", "?")
    nom = f"candidat.{modele}.cout_par_requete"
    profil = {champ: candidat.get(champ) for champ in CHAMPS_PROFIL_JETONS}
    if any(valeur is None for valeur in profil.values()):
        return Verification(
            nom=nom,
            section="resultats",
            conforme=False,
            detail="profil de jetons incomplet : le coût n'est pas recalculable",
        )
    try:
        recalcule = cout_recalcule(table, str(modele), region, niveau, a_la_date, profil)
    except (TarifInconnuError, KeyError, TypeError, ValueError) as erreur:
        return Verification(
            nom=nom, section="resultats", conforme=False, detail=f"coût non recalculable : {erreur}"
        )
    declare = candidat.get("cout_par_requete_recalcule_usd")
    return Verification(
        nom=nom,
        section="resultats",
        conforme=egal(declare, recalcule, TOLERANCE_COUT),
        attendu=recalcule,
        declare=declare,
        detail="coût par requête recalculé : jetons moyens × table des tarifs datée (Annexe D)",
    )
