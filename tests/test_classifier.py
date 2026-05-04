from app.processors.classifier import (
    OTHER_LABEL,
    build_corpus,
    classify_exhibitor,
    classify_text,
    keywords_from_corpus,
)


def test_classify_anti_drone_company():
    corpus = (
        "We produce battle-proven RF disruption systems and counter-UAV jamming "
        "platforms for homeland security clients."
    )
    labels = [c.label for c in classify_text(corpus)]
    assert "Anti-drones" in labels
    assert "Sécurité intérieure" in labels


def test_classify_cybersecurity():
    corpus = "Our SOC-as-a-service uses AI-powered SIEM for cyberdefense."
    labels = [c.label for c in classify_text(corpus)]
    assert "Cybersécurité" in labels
    assert "Intelligence artificielle" in labels


def test_unknown_text_returns_other():
    cls = classify_exhibitor({"presentation": "We sell luxury chocolate to airlines."})
    labels = [c.label for c in cls]
    assert labels == [OTHER_LABEL]


def test_corpus_combines_fields_and_finderr_labels():
    exh = {
        "company_name": "Foo",
        "one_liner": "ISR specialist",
        "presentation": None,
        "business_areas": ["DEFENSE"],
    }
    corpus = build_corpus(exh, ["UAV systems"])
    assert "ISR" in corpus
    assert "UAV" in corpus
    assert "DEFENSE" in corpus


def test_keywords_extraction():
    corpus = "AI-driven UAV solutions and ISR platforms with SIGINT capability."
    kw = keywords_from_corpus(corpus)
    assert "UAV" in kw
    assert "ISR" in kw
    assert "SIGINT" in kw
