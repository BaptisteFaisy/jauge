"""Jeu d'essai du dossier de preuve : une campagne fictive complète et exacte.

Ce module n'est pas collecté par pytest (nom sans préfixe `test_`) : il fournit
aux tests du « proof pack » un jeu de données partagé, comme
`tests/replay/constantes.py` le fait pour le rejeu. Tous les nombres sont
**calculés par le socle** (`intervalle_wilson`, `economie_mensuelle`,
`calculer_cout_requete`) : aucun chiffre n'est posé à la main, donc un test qui
falsifie une valeur le fait explicitement (ENF-IA-01).

`TEXTES_PLAN_DE_DONNEES` matérialise ce que le pack ne doit **jamais** contenir
(ENF-CONF-01) : les textes de requêtes, qui vivent dans le compte client.
"""

from __future__ import annotations

import hashlib
from datetime import date

from jauge.engine.stats import economie_mensuelle, intervalle_wilson
from jauge.models.campaign import Campagne, ResultatCandidat, StatutCampagne
from jauge.models.pricing import ServiceTier, TarifModele
from jauge.models.recommendation import (
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
    StatutRecommandation,
)
from jauge.models.tenant import Application, Tenant
from jauge.replay.executor import MesuresCandidat
from jauge.reports.proof_pack import EntreesPreuve, construire_proof_pack
from jauge.reports.proof_pack_entrees import (
    Approbation,
    EntreeCandidat,
    EntreeEchantillon,
    ResumeControle,
    ResumeRelecture,
)

# Identifiants fictifs de la démonstration (aucun modèle réel).
GRAND_MODELE = "demo-grand-modele"
PETIT_MODELE = "demo-petit-modele"
MODELE_JUGE = "demo-modele-juge"
REGION = "eu-west-1"
CLIENT_ID = "t-demo-a"
APPLICATION_ID = "ticket-classifier"
CAMPAGNE_ID = "c-proof-demo"
RECOMMANDATION_ID = "r-proof-demo"
DATE_REFERENCE = date(2026, 11, 12)
DATE_EFFET_TARIFS = date(2026, 1, 1)
HORODATAGE_GENERATION = "2026-11-12T10:00:00+00:00"
HORODATAGE_APPROBATION = "2026-11-12T09:30:00+01:00"
APPROBATEUR = "alice@client-a.example"

# Échantillon et effectifs de la démonstration.
TAILLE_ECHANTILLON = 40
SUCCES_REFERENCE = 39
SUCCES_CANDIDAT = 38
PAIRES_RELECTURE = 30
PAIRES_CONCORDANTES = 28
VOLUME_MENSUEL = 100_000
PART_MSP_POURCENTAGE = 20.0

# Jetons moyens par invocation (profil de la campagne fictive).
JETONS_ENTREE = 120
JETONS_SORTIE = 80
EXECUTIONS_REFERENCE = 2

# Tarifs Flex de démonstration, pour 1 000 jetons (mêmes montants que la table
# embarquée `data/pricing/bedrock-eu-west-1.json`).
PRIX_GRAND = (0.0075, 0.0375)
PRIX_PETIT = (0.0004, 0.0020)

#: Textes du plan de données : le pack ne doit en contenir **aucun** fragment.
TEXTES_PLAN_DE_DONNEES = {
    f"req-{index:04d}": (
        f"CONFIDENTIEL-CUSTOMER-{index}: voici le ticket client avec des données "
        f"personnelles (nom, adresse, numéro de contrat) à classer."
    )
    for index in range(TAILLE_ECHANTILLON)
}
#: Réponses candidates, elles aussi cantonnées au plan de données.
REPONSES_PLAN_DE_DONNEES = {
    identifiant: f'REPONSE-INTERNE-{index}: {{"etiquette": "facturation"}}'
    for index, identifiant in enumerate(TEXTES_PLAN_DE_DONNEES)
}


def empreinte_texte(texte: str) -> str:
    """Empreinte SHA-256 d'un texte, calculée côté plan de données."""
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def tarifs() -> list[TarifModele]:
    """Table des tarifs datée : deux modèles au niveau Flex du rejeu."""
    return [
        TarifModele(
            modele_id=modele,
            region=REGION,
            niveau=ServiceTier.FLEX,
            date_effet=DATE_EFFET_TARIFS,
            prix_entree_1k=entree,
            prix_sortie_1k=sortie,
        )
        for modele, (entree, sortie) in ((GRAND_MODELE, PRIX_GRAND), (PETIT_MODELE, PRIX_PETIT))
    ]


def cout_par_requete(prix: tuple[float, float]) -> float:
    """Coût d'une requête du jeu d'essai, d'après la formule de l'Annexe D."""
    entree, sortie = prix
    return round(JETONS_ENTREE / 1000.0 * entree + JETONS_SORTIE / 1000.0 * sortie, 8)


def tenant() -> Tenant:
    """Client de démonstration A."""
    return Tenant(
        id=CLIENT_ID,
        nom="Client A — Démo",
        compte_aws="123456789012",
        region_principale=REGION,
        role_arn="arn:aws:iam::123456789012:role/jauge-dataplane",
        external_id="ext-demo-a",
    )


def application() -> Application:
    """Application classée « surdimensionnée » : seuils du §13.4."""
    return Application(
        id=APPLICATION_ID,
        tenant_id=CLIENT_ID,
        nom="Classement de tickets",
        equipe="support",
        modele_actuel=GRAND_MODELE,
        seuil_qualite=0.95,
        seuil_intervalle_bas=0.90,
        description_tache="Classification en 6 catégories, sortie JSON",
    )


def campagne() -> Campagne:
    """Campagne terminée sur l'application de démonstration."""
    return Campagne(
        id=CAMPAGNE_ID,
        tenant_id=CLIENT_ID,
        application_id=APPLICATION_ID,
        modeles_candidats=[PETIT_MODELE],
        taille_echantillon=TAILLE_ECHANTILLON,
        modele_juge_id=MODELE_JUGE,
        statut=StatutCampagne.TERMINEE,
        cout_estime_usd=2.0,
        cout_reel_usd=1.4,
    )


def echantillon() -> list[EntreeEchantillon]:
    """Échantillon figé : identifiants, strates et empreintes de texte."""
    return [
        EntreeEchantillon(
            requete_id=identifiant,
            type_requete="classement",
            longueur_caracteres=len(texte),
            jetons_entree_origine=JETONS_ENTREE,
            strate="moyen:classement",
            empreinte_texte=empreinte_texte(texte),
        )
        for identifiant, texte in TEXTES_PLAN_DE_DONNEES.items()
    ]


def resultat(
    modele: str,
    succes: int,
    *,
    reference: bool,
    prix: tuple[float, float],
    echecs: int = 0,
    accord_humain: float | None = None,
    latences: tuple[int, int] = (820, 2100),
    familles: list[str] | None = None,
) -> ResultatCandidat:
    """Résultat consolidé d'un candidat, intervalle de Wilson inclus."""
    intervalle = intervalle_wilson(succes, TAILLE_ECHANTILLON)
    p50, p95 = latences
    return ResultatCandidat(
        modele_id=modele,
        est_reference=reference,
        nombre_requetes_rejouees=TAILLE_ECHANTILLON,
        taux_equivalence=intervalle.taux,
        intervalle_confiance_bas=intervalle.bas,
        intervalle_confiance_haut=intervalle.haut,
        auto_accord_modele_actuel=intervalle_wilson(SUCCES_REFERENCE, TAILLE_ECHANTILLON).taux,
        echecs_deterministes=echecs,
        accord_juge_humain=accord_humain,
        cout_par_requete_usd=cout_par_requete(prix),
        latence_p50_ms=p50,
        latence_p95_ms=p95,
        familles_en_echec=familles or [],
    )


def mesures(
    modele: str, *, reference: bool, prix: tuple[float, float], latences: tuple[int, int]
) -> MesuresCandidat:
    """Mesures du rejeu : totaux de jetons cohérents avec le profil moyen."""
    executions = TAILLE_ECHANTILLON * (EXECUTIONS_REFERENCE if reference else 1)
    cout = cout_par_requete(prix)
    p50, p95 = latences
    return MesuresCandidat(
        modele_id=modele,
        est_reference=reference,
        nombre_requetes_rejouees=TAILLE_ECHANTILLON,
        executions=executions,
        jetons_entree_total=JETONS_ENTREE * executions,
        jetons_sortie_total=JETONS_SORTIE * executions,
        cout_total_usd=round(cout * executions, 8),
        cout_par_requete_usd=cout,
        latence_p50_ms=p50,
        latence_p95_ms=p95,
    )


def candidats() -> list[EntreeCandidat]:
    """La référence (modèle actuel rejoué deux fois) et le candidat retenu."""
    accord = round(PAIRES_CONCORDANTES / PAIRES_RELECTURE, 6)
    reference = EntreeCandidat(
        modele_id=GRAND_MODELE,
        resultat=resultat(GRAND_MODELE, SUCCES_REFERENCE, reference=True, prix=PRIX_GRAND),
        mesures=mesures(GRAND_MODELE, reference=True, prix=PRIX_GRAND, latences=(820, 2100)),
        controles=(ResumeControle("longueur_bornee", TAILLE_ECHANTILLON, 0, critique=False),),
    )
    candidat = EntreeCandidat(
        modele_id=PETIT_MODELE,
        resultat=resultat(
            PETIT_MODELE,
            SUCCES_CANDIDAT,
            reference=False,
            prix=PRIX_PETIT,
            echecs=1,
            accord_humain=accord,
            latences=(410, 900),
            familles=["langue-rare"],
        ),
        mesures=mesures(PETIT_MODELE, reference=False, prix=PRIX_PETIT, latences=(410, 900)),
        controles=(
            ResumeControle("json_valide", TAILLE_ECHANTILLON, 1),
            ResumeControle("etiquette_identique", TAILLE_ECHANTILLON, 2),
            ResumeControle("longueur_bornee", TAILLE_ECHANTILLON, 0, critique=False),
        ),
    )
    return [reference, candidat]


def recommandation() -> Recommandation:
    """Recommandation au format de l'Annexe B, chiffres issus des candidats."""
    avant = cout_par_requete(PRIX_GRAND)
    apres = cout_par_requete(PRIX_PETIT)
    intervalle = intervalle_wilson(SUCCES_CANDIDAT, TAILLE_ECHANTILLON)
    auto_accord = intervalle_wilson(SUCCES_REFERENCE, TAILLE_ECHANTILLON).taux
    return Recommandation(
        id=RECOMMANDATION_ID,
        tenantId=CLIENT_ID,
        applicationId=APPLICATION_ID,
        currentModel=GRAND_MODELE,
        targetModel=PETIT_MODELE,
        campaignId=CAMPAGNE_ID,
        sampleSize=TAILLE_ECHANTILLON,
        quality=MetriquesQualite(
            equivalenceRate=intervalle.taux,
            confidenceIntervalLow=intervalle.bas,
            selfAgreementOfCurrentModel=auto_accord,
            deterministicFailures=1,
            judgeHumanAgreement=round(PAIRES_CONCORDANTES / PAIRES_RELECTURE, 6),
        ),
        cost=MetriquesCout(
            costPerRequestBefore=avant,
            costPerRequestAfter=apres,
            monthlyRequests=VOLUME_MENSUEL,
            expectedMonthlySaving=economie_mensuelle(avant, apres, VOLUME_MENSUEL),
        ),
        latency=MetriquesLatence(p50Before=820, p50After=410, p95Before=2100, p95After=900),
        risks=["Tickets in a rare language: 4 failures out of 5"],
        status=StatutRecommandation.APPROUVEE,
        approvedBy=APPROBATEUR,
        approvalNotes="approved after review of the 30-pair human sample",
    )


def relecture() -> ResumeRelecture:
    """Sous-échantillon de 30 paires relu dans le compte du client (§13.5)."""
    return ResumeRelecture(
        taille_sous_echantillon=PAIRES_RELECTURE,
        paires_relues=PAIRES_RELECTURE,
        paires_concordantes=PAIRES_CONCORDANTES,
        relecteur_id="relecteur-client-a",
        accord=round(PAIRES_CONCORDANTES / PAIRES_RELECTURE, 6),
    )


def approbation() -> Approbation:
    """Approbation humaine horodatée (ENF-IA-02)."""
    return Approbation(
        statut=StatutRecommandation.APPROUVEE.value,
        auteur=APPROBATEUR,
        horodatage=HORODATAGE_APPROBATION,
        notes="approved after review of the 30-pair human sample",
    )


def entrees_preuve(**surcharges: object) -> EntreesPreuve:
    """Entrées par défaut du pack, avec surcharges nommées pour les tests."""
    valeurs: dict[str, object] = {
        "campagne": campagne(),
        "application": application(),
        "recommandation": recommandation(),
        "candidats": candidats(),
        "echantillon": echantillon(),
        "tarifs": tarifs(),
        "tenant": tenant(),
        "a_la_date": DATE_REFERENCE,
        "relecture": relecture(),
        "approbation": approbation(),
        "part_msp_pourcentage": PART_MSP_POURCENTAGE,
        "genere_le": HORODATAGE_GENERATION,
    }
    valeurs.update(surcharges)
    return EntreesPreuve(**valeurs)  # type: ignore[arg-type]


def construire_pack(**surcharges: object):
    """Construit le pack de démonstration, avec surcharges nommées."""
    return construire_proof_pack(entrees_preuve(**surcharges))
