import pytest

from backend.app.language_detection import detect_language


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Qu'est-ce que les Français mangent le matin ? Une interview dans la rue avec des gens", "fr"),
        ("Cómo es la vida en Madrid para los jóvenes que no tienen trabajo", "es"),
        ("Come si vive in Italia: la vita di una famiglia che non ha molto", "it"),
        ("Was die Deutschen über das Wetter sagen und warum sie sich nicht beschweren", "de"),
        ("What people in London think about the weather and how they deal with it", "en"),
        ("Como é a vida em Lisboa para quem não tem muito dinheiro", "pt"),
        ("Что едят русские на завтрак? Интервью на улице", "ru"),
        ("日本の朝ごはんはこれです", "ja"),
        ("한국 사람들은 아침에 무엇을 먹을까요", "ko"),
        ("中国人早餐吃什么", "zh"),
        # Títulos cortos reales (casos que fallaban con un mínimo de 3 palabras).
        ("Come si vive in Italia con poco", "it"),
        ("Was die Deutschen über das Wetter sagen", "de"),
        ("Une journée dans la vie d'un boulanger", "fr"),
    ],
)
def test_detects_common_languages(text, expected):
    detection = detect_language(text)
    assert detection is not None and detection.language == expected


@pytest.mark.parametrize("text", [None, "", "   ", "Vlog #12", "Easy 123 | 2024", "Paris Vlog", "La la land"])
def test_returns_none_without_enough_evidence(text):
    assert detect_language(text) is None


def test_mixed_title_prefers_dominant_language():
    # Título en inglés + descripción larga en francés (caso típico de Easy French).
    text = "Easy French 123 | Street Interviews. Dans cette vidéo, nous avons demandé aux gens dans la rue ce qu'ils pensent de la vie à Paris et pourquoi ils aiment leur ville."
    assert detect_language(text).language == "fr"
