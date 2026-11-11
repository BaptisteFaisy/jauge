"""Schéma du rapport mensuel : ce qui est publié, et sous quelle forme.

Ces modèles pydantic sont le contrat du rapport (EF-RAP-03) : ils fixent les
champs du JSON remis au portail et les blocs lus par le gabarit Markdown. Ils
ne contiennent **aucun calcul** ; ils ne portent que des valeurs déjà fixées
par `jauge.reports.builder` (ENF-IA-01) et jamais de texte de requête ou de
réponse (ENF-CONF-01).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

# Constantes de présentation du rapport (aucun calcul métier ici).
DEVISE = "USD"
CONFIANCE_POURCENTAGE = 95.0
SEUIL_QUALITE_DEFAUT = 0.95
SEUIL_INTERVALLE_BAS_DEFAUT = 0.90
VERSION_SCHEMA = "1.0"


class LigneCout(BaseModel):
    """Ligne de dépense agrégée (application, équipe ou modèle)."""

    cle: str
    libelle: str
    cout_usd: float
    part_pourcentage: float = Field(ge=0.0, le=100.0)
    nombre_requetes: int = Field(default=0, ge=0)


class TendanceMensuelle(BaseModel):
    """Comparaison du mois du rapport avec le mois précédent (§4)."""

    cout_mois_courant_usd: float
    cout_mois_precedent_usd: float
    variation_usd: float
    variation_pourcentage: float
    sens: str  # "hausse" | "baisse" | "stable"


class EcartFacture(BaseModel):
    """Écart entre coût recalculé et facture sur la période (EF-ATT-06, §4)."""

    disponible: bool
    cout_recalcule_usd: float
    cout_facture_usd: float
    ecart_usd: float
    ecart_pourcentage: float
    conforme: bool
    jours_rapproches: int = Field(default=0, ge=0)
    causes_non_attribuees: list[str] = Field(default_factory=list)


class LevierRapporte(BaseModel):
    """Levier d'économie détecté, avec son économie estimée (Annexe A)."""

    id: str
    application_id: str
    application_nom: str
    type_levier: str
    statut: str
    economie_mensuelle_estimee_usd: float
    confiance: float
    applique: bool


class EconomiesRapportees(BaseModel):
    """Économies attendues, constatées et restantes (§4, §7.2, EF-VER-02).

    `attendues_usd` : somme des économies mensuelles des recommandations
    engagées (approuvées, en bascule ou appliquées) — l'engagement pris.
    `constatees_usd` : somme des économies vérifiées sur la facture à 30 jours.
    Les deux champs restent distincts : on ne présente jamais une promesse
    comme une réalisation.
    """

    attendues_usd: float
    constatees_usd: float
    restantes_usd: float
    ecart_usd: float
    taux_realisation_pourcentage: float
    nombre_verifications: int = Field(default=0, ge=0)


class RecommandationEnAttente(BaseModel):
    """Recommandation soumise au responsable d'application (§8.5)."""

    id: str
    application_id: str
    application_nom: str
    modele_actuel: str
    modele_cible: str
    economie_mensuelle_attendue_usd: float
    statut: str


class EtatBascule(BaseModel):
    """État d'une bascule progressive par règle de passerelle (§8.6)."""

    id: str
    application_id: str
    application_nom: str
    modele_cible: str
    palier_actuel_pourcentage: int
    actif: bool
    annule_automatiquement: bool
    qualite_en_ligne_mesuree: float
    seuil_alerte: float
    motif_annulation: str | None = None


class AppendiceMethodologique(BaseModel):
    """Ce qu'il faut rappeler au lecteur pour juger les chiffres (§13)."""

    seuil_qualite: float
    seuil_intervalle_bas: float
    taille_echantillon: int = Field(ge=0)
    intervalle_confiance_pourcentage: float
    z_score: float
    date_table_tarifs: date | None = None
    region: str
    devise: str = DEVISE
    sources: list[str] = Field(default_factory=list)


class SyntheseRapport(BaseModel):
    """Texte de synthèse et sa provenance (EF-RAP-03, ENF-IA-01).

    `source` vaut `deterministe` quand le texte est produit par le code (repli
    ou démonstration) et `modele` quand un modèle l'a rédigé. Le texte n'entre
    dans aucun calcul : il orne des chiffres déjà fixés.
    """

    texte: str
    source: str
    modele: str | None = None
    degradee: bool = False


class RapportMensuel(BaseModel):
    """Rapport mensuel complet d'un client pour un mois donné (EF-RAP-03)."""

    schema_version: str = VERSION_SCHEMA
    id: str
    tenant_id: str
    tenant_nom: str
    annee: int
    mois: int
    periode_debut: date
    periode_fin: date
    devise: str = DEVISE
    cout_total_usd: float
    nombre_requetes: int = Field(default=0, ge=0)
    couverture_attribution_pourcentage: float
    par_application: list[LigneCout] = Field(default_factory=list)
    par_equipe: list[LigneCout] = Field(default_factory=list)
    par_modele: list[LigneCout] = Field(default_factory=list)
    tendance: TendanceMensuelle
    ecart_facture: EcartFacture
    leviers: list[LevierRapporte] = Field(default_factory=list)
    economies: EconomiesRapportees
    recommandations_en_attente: list[RecommandationEnAttente] = Field(default_factory=list)
    bascules: list[EtatBascule] = Field(default_factory=list)
    methodologie: AppendiceMethodologique
    synthese: SyntheseRapport | None = None
