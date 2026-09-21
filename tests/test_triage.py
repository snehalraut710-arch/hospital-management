"""The triage engine.

These run against the real trained model, because a test of a classifier that
mocks the classifier tests nothing.
"""
import pytest

from hospital.services import triage
from hospital.services.triage import KBRule


@pytest.fixture(scope="module")
def model():
    from config import Config
    return triage.get_model(Config.MODEL_PATH, Config.TRAINING_DATA)


@pytest.fixture(scope="module")
def rules():
    from hospital.data.knowledge_base import SYMPTOM_KB
    return [KBRule(d, p, w, rf, a) for d, p, w, rf, a in SYMPTOM_KB]


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
class TestNormalise:
    def test_lowercases_and_strips_punctuation(self):
        assert triage.normalise("Chest PAIN!!!") == "chest pain"

    def test_expands_lay_synonyms(self):
        assert "abdomen" in triage.normalise("my tummy hurts")

    def test_prefers_the_longer_phrase(self):
        # "stomach ache" must win over a bare "stomach" substitution
        assert "abdomen pain" in triage.normalise("I have a stomach ache")

    def test_expands_abbreviations(self):
        assert "blood pressure" in triage.normalise("my BP is high")

    def test_empty_input_is_safe(self):
        assert triage.normalise("") == ""
        assert triage.normalise(None) == ""

    def test_content_words_drop_fillers(self):
        words = triage.content_words("I have a really bad pain")
        assert "pain" in words
        assert "have" not in words
        assert "really" not in words


# ---------------------------------------------------------------------------
# Red flags -- the safety-critical path
# ---------------------------------------------------------------------------
class TestRedFlags:
    CRITICAL = [
        ("I have chest pain radiating to my left arm", "Cardiology"),
        ("my father has sudden slurred speech and face drooping", "Neurology"),
        ("I have been coughing up blood", "Pulmonology"),
        ("I am vomiting blood", "Gastroenterology"),
    ]

    @pytest.mark.parametrize("text,department", CRITICAL)
    def test_emergency_presentations_route_correctly(self, model, rules, text, department):
        result = triage.classify(text, model=model, rules=rules)
        assert result.is_red_flag, f"{text!r} should have been flagged"
        assert result.action == "emergency"
        assert result.department == department

    def test_red_flag_carries_advice(self, model, rules):
        result = triage.classify(
            "crushing chest pain with cold sweat", model=model, rules=rules
        )
        assert result.red_flag_advice
        assert "emergency" in result.red_flag_advice.lower()

    def test_self_harm_is_flagged_with_crisis_guidance(self, model, rules):
        result = triage.classify("I have thoughts of harming myself", model=model, rules=rules)
        assert result.is_red_flag
        assert result.department == "Psychiatry"
        assert "helpline" in result.red_flag_advice.lower()

    def test_red_flag_beats_a_confident_wrong_model(self, model, rules):
        """The whole point of the deterministic layer.

        Even where the wording leans elsewhere, the red-flag rule must win.
        """
        result = triage.classify(
            "skin rash and also chest pain radiating to my left arm",
            model=model, rules=rules,
        )
        assert result.department == "Cardiology"


# ---------------------------------------------------------------------------
# Confidence tiers
# ---------------------------------------------------------------------------
class TestConfidenceTiers:
    def test_clear_symptoms_are_recommended_outright(self, model, rules):
        result = triage.classify(
            "itchy red rash on my arms with dry flaky skin", model=model, rules=rules
        )
        assert result.action == "recommend"
        assert result.confidence >= 0.60
        assert result.department == "Dermatology"

    def test_empty_input_falls_back_and_asks(self, model, rules):
        result = triage.classify("   ", model=model, rules=rules)
        assert result.action == "fallback"
        assert result.clarifying_question

    def test_greeting_only_falls_back(self, model, rules):
        result = triage.classify("hello there", model=model, rules=rules)
        assert result.action == "fallback"

    def test_thresholds_are_respected(self, model, rules):
        """Raising the bar to an impossible level must force a fallback."""
        result = triage.classify(
            "itchy rash", model=model, rules=rules,
            confident_threshold=1.01, clarify_threshold=1.005,
        )
        assert result.action == "fallback"
        assert result.department == "General Medicine"

    def test_a_clarify_tier_asks_a_question(self, model, rules):
        """Force the middle tier by moving the thresholds around the score."""
        result = triage.classify(
            "I feel some discomfort", model=model, rules=rules,
            confident_threshold=0.99, clarify_threshold=0.01,
        )
        assert result.action == "clarify"
        assert result.clarifying_question


# ---------------------------------------------------------------------------
# Explanation and scoring
# ---------------------------------------------------------------------------
class TestExplanation:
    def test_explains_using_words_from_the_input(self, model, rules):
        result = triage.classify(
            "severe migraine with nausea and light sensitivity", model=model, rules=rules
        )
        assert result.matched_terms
        source = triage.normalise(result.query)
        for term in result.matched_terms:
            assert all(word in source for word in term.split()), term

    def test_explanation_contains_no_pure_filler(self, model, rules):
        from hospital.data.synonyms import FILLERS
        result = triage.classify("I have a very bad rash on my arms", model=model, rules=rules)
        for term in result.matched_terms:
            assert not all(word in FILLERS for word in term.split()), term

    def test_scores_are_a_normalised_distribution(self, model, rules):
        result = triage.classify("persistent cough and wheezing", model=model, rules=rules)
        assert abs(sum(s.score for s in result.scores) - 1.0) < 1e-6

    def test_scores_are_ranked(self, model, rules):
        result = triage.classify("knee pain when climbing stairs", model=model, rules=rules)
        scores = [s.score for s in result.scores]
        assert scores == sorted(scores, reverse=True)

    def test_result_serialises_with_a_disclaimer(self, model, rules):
        payload = triage.classify("sore throat", model=model, rules=rules).to_dict()
        assert "diagnosis" in payload["disclaimer"].lower()
        assert 0 <= payload["percent"] <= 100


# ---------------------------------------------------------------------------
# Availability filtering
# ---------------------------------------------------------------------------
class TestAvailableDepartments:
    def test_only_staffed_departments_are_recommended(self, model, rules):
        result = triage.classify(
            "chest pain and palpitations", model=model, rules=rules,
            available_departments=["Dermatology", "General Medicine"],
        )
        assert result.department in ("Dermatology", "General Medicine")
        assert all(s.department in ("Dermatology", "General Medicine") for s in result.scores)


# ---------------------------------------------------------------------------
# The keyword layer in isolation
# ---------------------------------------------------------------------------
class TestKeywordLayer:
    def test_weighted_matching_normalises_to_one(self):
        rules = [
            KBRule("Cardiology", "chest pain", 2.0),
            KBRule("Pulmonology", "cough", 1.0),
        ]
        scores = triage.keyword_scores("chest pain with a cough", rules)
        assert abs(sum(scores.values()) - 1.0) < 1e-9
        assert scores["Cardiology"] > scores["Pulmonology"]

    def test_no_match_returns_nothing(self):
        assert triage.keyword_scores("unrelated words", [KBRule("ENT", "tinnitus")]) == {}

    def test_red_flag_rules_are_excluded_from_blending(self):
        """Red flags act as an override, so they must not also skew the blend."""
        rules = [KBRule("Cardiology", "chest pain", 3.0, is_red_flag=True)]
        assert triage.keyword_scores("chest pain", rules) == {}

    def test_finds_the_heaviest_red_flag(self):
        rules = [
            KBRule("Cardiology", "chest pain", 1.0, is_red_flag=True),
            KBRule("Cardiology", "chest pain radiating", 3.0, is_red_flag=True),
        ]
        assert triage.find_red_flag("chest pain radiating to arm", rules).weight == 3.0


# ---------------------------------------------------------------------------
# Model quality -- a regression guard, not a leaderboard
# ---------------------------------------------------------------------------
class TestModelQuality:
    def test_cross_validated_accuracy_clears_the_floor(self):
        from config import Config
        _, accuracy = triage.train_model(Config.TRAINING_DATA, model_path=None)
        assert accuracy > 0.55, f"model accuracy regressed to {accuracy:.1%}"

    def test_blended_engine_beats_the_model_alone(self, model, rules):
        """The knowledge base must be earning its 30% of the score."""
        import csv
        from config import Config

        with open(Config.TRAINING_DATA, encoding="utf-8") as fh:
            rows = [(r["text"], r["department"]) for r in csv.DictReader(fh)]

        blended_hits = model_only_hits = 0
        for text, expected in rows:
            if triage.classify(text, model=model, rules=rules).department == expected:
                blended_hits += 1
            if triage.classify(
                text, model=model, rules=rules, model_weight=1.0, kb_weight=0.0
            ).department == expected:
                model_only_hits += 1

        assert blended_hits >= model_only_hits
