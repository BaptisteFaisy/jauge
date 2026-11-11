"""Tests du modèle juge (EF-EVA-01, §13.3, §13.5, §3.2).

Aucun appel réseau : `BedrockJugeClient` n'est jamais construit avec un client
boto3 réel, on ne vérifie que la préparation de la tâche d'évaluation et la
traduction note → verdict.
"""

from __future__ import annotations

import pytest

from jauge.common.errors import ValidationError
from jauge.evaluation.judge import (
    NOM_METRIQUE,
    BedrockJugeClient,
    DemandeNotation,
    FakeJugeClient,
    RegistreCapacite,
    VerdictJuge,
    choisir_juge,
    construire_prompt,
    lignes_jeu_donnees,
    valider_juge,
    verdict_depuis_enregistrement,
    verdict_depuis_note,
)

# Catalogue fictif : rangs croissants, du plus petit au plus grand modèle.
CATALOGUE = {
    "petit-modele": 1,
    "moyen-modele": 2,
    "grand-modele": 3,
    "tres-grand-modele": 4,
}

# Sortie d'évaluation fictive : une paire jugée non équivalente, avec explication.
LIGNE_RESULTAT = '{"evaluationResults": [{"result": 0.0, "explanation": "hors sujet"}]}'


class _Corps:
    """Corps d'objet S3 minimal (interface boto3 `Body.read()`)."""

    def __init__(self, donnees: bytes) -> None:
        self._donnees = donnees

    def read(self) -> bytes:
        """Rend le contenu brut de l'objet."""
        return self._donnees


class _FauxBedrock:
    """Client Bedrock fictif : la tâche est terminée dès la première lecture."""

    def __init__(self) -> None:
        self.payload: dict[str, object] = {}

    def create_evaluation_job(self, **kwargs: object) -> dict[str, str]:
        """Retient le payload et rend un ARN de tâche."""
        self.payload = dict(kwargs)
        return {"jobArn": "arn:aws:bedrock:eu-west-1:123:job/jauge-demo/tache-1"}

    def get_evaluation_job(self, jobIdentifier: str) -> dict[str, str]:
        """Statut de la tâche : terminée (aucune attente en test)."""
        return {"status": "Completed"}


class _FauxS3:
    """Client S3 fictif : un seul objet de résultats."""

    def list_objects_v2(self, Bucket: str, Prefix: str) -> dict[str, list[dict[str, str]]]:
        """Liste l'objet de résultats de la tâche."""
        return {"Contents": [{"Key": f"{Prefix}/resultats.jsonl"}]}

    def get_object(self, Bucket: str, Key: str) -> dict[str, _Corps]:
        """Rend le contenu de l'objet de résultats."""
        return {"Body": _Corps(LIGNE_RESULTAT.encode("utf-8"))}


@pytest.fixture
def registre() -> RegistreCapacite:
    """Registre de capacité du catalogue fictif."""
    return RegistreCapacite(CATALOGUE)


@pytest.fixture
def demande() -> DemandeNotation:
    """Paire type soumise au juge."""
    return DemandeNotation(
        requete_id="q-1",
        requete="Classe ce ticket : « je n'arrive pas à me connecter »",
        reponse_reference='{"etiquette": "acces"}',
        reponse_candidate='{"etiquette": "acces"}',
        type_tache="classification",
    )


def test_prompt_demande_le_meme_service() -> None:
    """Le prompt pose la question d'équivalence du §13.3 et porte les trois textes."""
    prompt = construire_prompt(
        DemandeNotation(
            requete_id="q-2",
            requete="REQUETE-A",
            reponse_reference="REFERENCE-B",
            reponse_candidate="CANDIDATE-C",
        )
    )
    assert "same service" in prompt
    assert "REQUETE-A" in prompt
    assert "REFERENCE-B" in prompt
    assert "CANDIDATE-C" in prompt


def test_prompt_interdit_au_juge_de_calculer_un_nombre() -> None:
    """ENF-IA-01 : le juge ne calcule ni taux, ni pourcentage, ni coût."""
    prompt = construire_prompt(
        DemandeNotation(requete_id="q", requete="r", reponse_reference="a", reponse_candidate="b")
    )
    assert "Do not compute any rate" in prompt
    assert '"equivalent": true or false' in prompt


def test_choisir_juge_ecarte_les_candidats(registre: RegistreCapacite) -> None:
    """§13.5 : le juge n'est jamais l'un des candidats comparés."""
    juge = choisir_juge("moyen-modele", ["grand-modele"], registre)
    assert juge != "grand-modele"
    assert registre.au_moins_aussi_capable(juge, "moyen-modele")


def test_choisir_juge_au_moins_aussi_capable_que_le_modele_actuel(
    registre: RegistreCapacite,
) -> None:
    """§13.5 : un juge plus faible que le modèle actuel est écarté."""
    juge = choisir_juge("grand-modele", ["petit-modele", "moyen-modele"], registre)
    assert registre.rang(juge) >= registre.rang("grand-modele")


def test_choisir_juge_prefere_un_modele_distinct_du_modele_actuel(
    registre: RegistreCapacite,
) -> None:
    """À capacité suffisante, le juge n'est pas le modèle actuel : il reste indépendant."""
    juge = choisir_juge("moyen-modele", ["petit-modele"], registre)
    assert juge == "tres-grand-modele"  # distinct du modèle actuel, le plus capable


def test_choisir_juge_se_rabat_sur_le_modele_actuel_en_dernier_recours(
    registre: RegistreCapacite,
) -> None:
    """Aucun autre modèle assez capable : le modèle actuel sert de juge (jamais un candidat)."""
    juge = choisir_juge(
        "moyen-modele", ["petit-modele", "grand-modele", "tres-grand-modele"], registre
    )
    assert juge == "moyen-modele"
    valider_juge(juge, "moyen-modele", ["petit-modele"], registre)


def test_choisir_juge_refuse_un_catalogue_insuffisant(registre: RegistreCapacite) -> None:
    """Le modèle actuel est lui-même candidat et aucun autre n'est assez capable : refus."""
    with pytest.raises(ValidationError) as erreur:
        choisir_juge(
            "moyen-modele", ["moyen-modele", "grand-modele", "tres-grand-modele"], registre
        )
    assert erreur.value.code == "validation_error"


def test_valider_juge_refuse_un_candidat_comme_juge(registre: RegistreCapacite) -> None:
    """Un candidat ne peut pas se noter lui-même (§13.5)."""
    with pytest.raises(ValidationError):
        valider_juge("grand-modele", "moyen-modele", ["grand-modele"], registre)


def test_valider_juge_refuse_un_juge_moins_capable(registre: RegistreCapacite) -> None:
    """Un juge plus faible que le modèle actuel fausse la mesure (§13.5)."""
    with pytest.raises(ValidationError):
        valider_juge("petit-modele", "grand-modele", ["moyen-modele"], registre)


def test_valider_juge_accepte_le_cas_nominal(registre: RegistreCapacite) -> None:
    """Juge distinct des candidats et au moins aussi capable : accepté."""
    valider_juge("tres-grand-modele", "moyen-modele", ["petit-modele"], registre)


def test_registre_refuse_un_modele_inconnu(registre: RegistreCapacite) -> None:
    """Un modèle absent du catalogue ne peut pas être choisi en silence."""
    with pytest.raises(ValidationError):
        registre.rang("modele-inconnu")


def test_fake_juge_rend_les_verdicts_parametres(demande: DemandeNotation) -> None:
    """`FakeJugeClient` rend le verdict fixé par `requete_id`, sinon le défaut."""
    juge = FakeJugeClient(
        verdicts={"q-1": VerdictJuge(equivalent=False, explication="hors sujet")},
        verdict_defaut=VerdictJuge(equivalent=True, explication="défaut"),
    )
    autre = DemandeNotation("q-2", "r", "a", "b")
    verdicts = juge.noter_lot([demande, autre])
    assert verdicts[0] == VerdictJuge(equivalent=False, explication="hors sujet")
    assert verdicts[1].equivalent is True
    assert len(juge.appels) == 2


def test_fake_juge_strategie_personnalisee(demande: DemandeNotation) -> None:
    """Une stratégie permet de simuler un juge systématiquement négatif."""
    juge = FakeJugeClient(strategie=lambda _: VerdictJuge(equivalent=False, explication="non"))
    assert juge.noter_lot([demande])[0].equivalent is False


def test_bedrock_note_un_lot_avec_des_clients_fictifs(demande: DemandeNotation) -> None:
    """L'adaptateur réel est vérifié par son interface, avec des clients fictifs."""
    client = BedrockJugeClient(
        modele_id="juge-modele",
        region="eu-west-1",
        modele_candidat_id="candidat-modele",
        role_arn="arn:aws:iam::123456789012:role/jauge-eval",
        uri_sortie_s3="s3://jauge-eval/sortie",
        nom_dataset="jauge-demo",
        client=_FauxBedrock(),
        client_s3=_FauxS3(),
    )
    verdicts = client.noter_lot([demande])
    assert verdicts == [VerdictJuge(equivalent=False, explication="hors sujet")]


def test_bedrock_prepare_la_tache_evaluation(demande: DemandeNotation) -> None:
    """§3.2 : le jeu de données fournit les réponses, Bedrock saute l'invocation."""
    client = BedrockJugeClient(
        modele_id="juge-modele",
        region="eu-west-1",
        modele_candidat_id="candidat-modele",
        role_arn="arn:aws:iam::123456789012:role/jauge-eval",
        uri_sortie_s3="s3://jauge-eval/sortie",
        nom_dataset="jauge-demo",
    )
    payload = client.preparer_tache_evaluation([demande])
    config = payload["evaluationConfig"]["automated"]["datasetMetricConfigs"][0]
    assert config["customMetric"]["metricName"] == NOM_METRIQUE
    assert (
        payload["inferenceConfig"]["models"][0]["precomputedInferenceSource"][
            "inferenceSourceIdentifier"
        ]
        == "candidat-modele"
    )
    assert payload["outputDataConfig"]["s3Uri"] == "s3://jauge-eval/sortie"


def test_bedrock_refuse_une_tache_vide() -> None:
    """Une tâche d'évaluation sans paire est une erreur de programmation."""
    client = BedrockJugeClient("j", "eu-west-1", "c", "arn", "s3://b/p", "ds")
    with pytest.raises(ValidationError):
        client.preparer_tache_evaluation([])


def test_lignes_jeu_donnees_fournissent_les_reponses(demande: DemandeNotation) -> None:
    """Les réponses candidates voyagent dans le jeu de données (§3.2)."""
    lignes = lignes_jeu_donnees([demande])
    assert lignes[0]["referenceResponse"] == demande.reponse_reference
    assert lignes[0]["modelResponses"][0]["output"] == demande.reponse_candidate


def test_verdict_depuis_note_utilise_le_seuil_du_code() -> None:
    """Le seuil note → verdict est une constante du code, pas une sortie de LLM."""
    assert verdict_depuis_note(1.0).equivalent is True
    assert verdict_depuis_note(0.5).equivalent is True
    assert verdict_depuis_note(0.0).equivalent is False


def test_verdict_depuis_enregistrement_conserve_l_explication() -> None:
    """L'explication du juge est conservée pour chaque paire (§13.5)."""
    verdict = verdict_depuis_enregistrement(
        {"evaluationResults": [{"result": 0.0, "explanation": "réponse hors sujet"}]}
    )
    assert verdict.equivalent is False
    assert verdict.explication == "réponse hors sujet"
