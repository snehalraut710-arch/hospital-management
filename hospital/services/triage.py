"""Triage engine: free-text symptoms -> a recommended department.

Works in two layers. The classifier (word + char TF-IDF into logistic
regression) handles the general case, and the symptom_kb rules back it up on
wording the training data never covered. Red-flag rules override the classifier
completely.

Score = MODEL_WEIGHT * P(department|text) + KB_WEIGHT * keyword_score, with both
weights in config.py.

Nothing here touches Flask or the database, so it can be tested on its own.
"""
from __future__ import annotations

import csv
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import FeatureUnion, Pipeline

from hospital.data.knowledge_base import CLARIFYING_QUESTIONS
from hospital.data.synonyms import FILLERS, SYNONYMS

_model_lock = threading.Lock()
_model = None
_model_path_loaded: str | None = None


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class DepartmentScore:
    department: str
    score: float
    model_probability: float
    keyword_score: float

    @property
    def percent(self) -> int:
        return int(round(self.score * 100))


@dataclass
class TriageResult:
    """What the engine concluded, and why."""

    query: str
    normalised: str
    department: str
    confidence: float
    action: str                       # recommend | clarify | fallback | emergency
    scores: list[DepartmentScore] = field(default_factory=list)
    is_red_flag: bool = False
    red_flag_advice: str | None = None
    matched_terms: list[str] = field(default_factory=list)
    runner_up: str | None = None
    clarifying_question: str | None = None

    DISCLAIMER = (
        "This is triage guidance to help you reach the right department. "
        "It is not a diagnosis. If your symptoms are severe or worsening, "
        "seek emergency care."
    )

    @property
    def percent(self) -> int:
        return int(round(self.confidence * 100))

    @property
    def top_three(self) -> list[DepartmentScore]:
        return self.scores[:3]

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "department": self.department,
            "confidence": round(self.confidence, 3),
            "percent": self.percent,
            "action": self.action,
            "is_red_flag": self.is_red_flag,
            "red_flag_advice": self.red_flag_advice,
            "matched_terms": self.matched_terms,
            "runner_up": self.runner_up,
            "clarifying_question": self.clarifying_question,
            "scores": [
                {"department": s.department, "percent": s.percent}
                for s in self.top_three
            ],
            "disclaimer": self.DISCLAIMER,
        }


@dataclass
class KBRule:
    """A knowledge-base rule, decoupled from the SQLAlchemy model so the engine
    can be tested without a database."""
    department: str
    phrase: str
    weight: float = 1.0
    is_red_flag: bool = False
    advice: str | None = None


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------
_PUNCT = re.compile(r"[^a-z0-9\s]")
_WS = re.compile(r"\s+")

# Longest phrases first, so "stomach ache" wins over "stomach".
_SORTED_SYNONYMS = sorted(SYNONYMS.items(), key=lambda kv: -len(kv[0]))


def normalise(text: str) -> str:
    """Lowercase, strip punctuation, and expand lay terms into clinical ones."""
    if not text:
        return ""
    out = _PUNCT.sub(" ", text.lower())
    out = _WS.sub(" ", out).strip()
    for lay, clinical in _SORTED_SYNONYMS:
        if lay in out:
            out = out.replace(lay, clinical)
    return _WS.sub(" ", out).strip()


def content_words(text: str) -> list[str]:
    """Drop filler words, which would otherwise dilute the keyword score."""
    return [w for w in normalise(text).split() if w not in FILLERS and len(w) > 2]


# ---------------------------------------------------------------------------
# Model training and loading
# ---------------------------------------------------------------------------
def build_pipeline() -> Pipeline:
    """Word + character TF-IDF into logistic regression.

    Picked by cross-validating this against Multinomial NB, Complement NB and
    LinearSVC (see docs/05-ai-engine.md for the numbers).

    Char n-grams cope with misspellings and word endings ("breathless" vs
    "breathlessness") that word features treat as unknown tokens. Word bigrams
    keep "chest pain" and "back pain" apart instead of collapsing them to
    "pain".
    """
    return Pipeline([
        ("features", FeatureUnion([
            ("word", TfidfVectorizer(
                ngram_range=(1, 2), sublinear_tf=True, min_df=1,
            )),
            ("char", TfidfVectorizer(
                analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=1,
            )),
        ])),
        ("clf", LogisticRegression(C=10, max_iter=3000, class_weight="balanced")),
    ])


def load_training_data(csv_path: str) -> tuple[list[str], list[str]]:
    texts, labels = [], []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            text = (row.get("text") or "").strip()
            label = (row.get("department") or "").strip()
            if text and label:
                texts.append(normalise(text))
                labels.append(label)
    if not texts:
        raise ValueError(f"No training rows found in {csv_path}")
    return texts, labels


def train_model(csv_path: str, model_path: str | None = None) -> tuple[Pipeline, float]:
    """Train the classifier, save it, and return (pipeline, accuracy).

    Accuracy comes from 5-fold cross-validation, then the model is refit on all
    the data. With only a few hundred rows a single train/test split gave a very
    noisy number, so cross-validation is the more honest figure to quote.
    """
    texts, labels = load_training_data(csv_path)

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    accuracy = float(cross_val_score(build_pipeline(), texts, labels, cv=cv).mean())

    final = build_pipeline()
    final.fit(texts, labels)

    if model_path:
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        joblib.dump(final, model_path)

    global _model, _model_path_loaded
    with _model_lock:
        _model = final
        _model_path_loaded = model_path
    return final, accuracy


def get_model(model_path: str, csv_path: str) -> Pipeline:
    """Return the cached model, loading from disk or training if necessary."""
    global _model, _model_path_loaded
    with _model_lock:
        if _model is not None and _model_path_loaded == model_path:
            return _model
    if os.path.exists(model_path):
        try:
            model = joblib.load(model_path)
            with _model_lock:
                _model = model
                _model_path_loaded = model_path
            return model
        except Exception:  # corrupt or version-mismatched pickle: retrain
            pass
    model, _ = train_model(csv_path, model_path)
    return model


def reset_model_cache() -> None:
    """Used by tests, and after retraining from the CLI."""
    global _model, _model_path_loaded
    with _model_lock:
        _model = None
        _model_path_loaded = None


# ---------------------------------------------------------------------------
# Explanation
# ---------------------------------------------------------------------------
def explain(model: Pipeline, text: str, department: str, limit: int = 4) -> list[str]:
    """Return the input terms that pushed hardest toward the chosen department.

    Contribution = the term's TF-IDF value * the model's coefficient for it
    under that department. Only word features are returned; char n-grams score
    well but read as nonsense ("che", "st p") so they are no use as an
    explanation.
    """
    try:
        features: FeatureUnion = model.named_steps["features"]
        clf: LogisticRegression = model.named_steps["clf"]
        classes = list(clf.classes_)
        if department not in classes:
            return []
        idx = classes.index(department)

        row = features.transform([text])
        names = features.get_feature_names_out()
        # Binary problems keep a single coefficient row; multiclass has one per class.
        coefs = clf.coef_[idx] if clf.coef_.shape[0] > 1 else clf.coef_[0]

        contributions = []
        for col in row.nonzero()[1]:
            name = names[col]
            if not name.startswith("word__"):
                continue
            term = name[len("word__"):]
            # A term made only of filler words explains nothing to a reader,
            # even when the model genuinely leans on it.
            if all(part in FILLERS or len(part) <= 2 for part in term.split()):
                continue
            contributions.append((coefs[col] * row[0, col], term))

        contributions.sort(reverse=True)
        terms: list[str] = []
        for score, term in contributions:
            if score <= 0:
                break
            # Prefer the more specific phrase; drop unigrams a kept bigram covers.
            if any(term in kept or kept in term for kept in terms):
                continue
            terms.append(term)
            if len(terms) >= limit:
                break
        return terms
    except Exception:
        return []


# ---------------------------------------------------------------------------
# The keyword layer
# ---------------------------------------------------------------------------
def keyword_scores(text: str, rules: Iterable[KBRule]) -> dict[str, float]:
    """Weighted phrase matching, normalised to 0..1 across departments."""
    normalised = normalise(text)
    raw: dict[str, float] = {}
    for rule in rules:
        if rule.is_red_flag:
            continue  # red flags are handled separately, not blended
        phrase = normalise(rule.phrase)
        if phrase and phrase in normalised:
            raw[rule.department] = raw.get(rule.department, 0.0) + rule.weight
    if not raw:
        return {}
    total = sum(raw.values())
    return {dept: value / total for dept, value in raw.items()}


def find_red_flag(text: str, rules: Iterable[KBRule]) -> KBRule | None:
    """Highest-weight red-flag rule matching the text, if any."""
    normalised = normalise(text)
    hits = [
        r for r in rules
        if r.is_red_flag and normalise(r.phrase) and normalise(r.phrase) in normalised
    ]
    if not hits:
        return None
    return max(hits, key=lambda r: r.weight)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------
def classify(
    text: str,
    *,
    model: Pipeline,
    rules: Sequence[KBRule] = (),
    available_departments: Sequence[str] | None = None,
    model_weight: float = 0.7,
    kb_weight: float = 0.3,
    confident_threshold: float = 0.60,
    clarify_threshold: float = 0.35,
    fallback_department: str = "General Medicine",
) -> TriageResult:
    """Route free-text symptoms to a department.

    ``available_departments`` restricts the answer to departments the hospital
    actually staffs -- there is no point recommending Neurology if no
    neurologist is registered.
    """
    query = (text or "").strip()
    normalised = normalise(query)

    if len(content_words(query)) == 0:
        return TriageResult(
            query=query, normalised=normalised,
            department=fallback_department, confidence=0.0, action="fallback",
            clarifying_question="Could you describe what you are experiencing, in your own words?",
        )

    # -- Layer 1: red flags override everything -------------------------
    red_flag = find_red_flag(query, rules)

    # -- Layer 2: statistical -------------------------------------------
    probabilities = dict(
        zip(model.named_steps["clf"].classes_, model.predict_proba([normalised])[0])
    )

    # -- Layer 3: keyword ------------------------------------------------
    keywords = keyword_scores(query, rules)

    allowed = set(available_departments) if available_departments else None
    blended: list[DepartmentScore] = []
    for dept in set(probabilities) | set(keywords):
        if allowed is not None and dept not in allowed:
            continue
        model_p = float(probabilities.get(dept, 0.0))
        kb_p = float(keywords.get(dept, 0.0))
        blended.append(DepartmentScore(
            department=dept,
            score=model_weight * model_p + kb_weight * kb_p,
            model_probability=model_p,
            keyword_score=kb_p,
        ))

    if not blended:
        return TriageResult(
            query=query, normalised=normalised,
            department=fallback_department, confidence=0.0, action="fallback",
        )

    # Renormalise so the reported confidence is a share of the total, which is
    # what a reader assumes a percentage means.
    total = sum(s.score for s in blended) or 1.0
    for s in blended:
        s.score /= total
    blended.sort(key=lambda s: s.score, reverse=True)

    top = blended[0]
    runner_up = blended[1].department if len(blended) > 1 else None

    if red_flag and (allowed is None or red_flag.department in allowed):
        return TriageResult(
            query=query, normalised=normalised,
            department=red_flag.department,
            confidence=max(top.score, 0.9),
            action="emergency",
            scores=blended,
            is_red_flag=True,
            red_flag_advice=red_flag.advice,
            matched_terms=[red_flag.phrase],
            runner_up=runner_up,
        )

    terms = explain(model, normalised, top.department)

    if top.score >= confident_threshold:
        action = "recommend"
        question = None
    elif top.score >= clarify_threshold:
        action = "clarify"
        target = runner_up or top.department
        question = CLARIFYING_QUESTIONS.get(target) or CLARIFYING_QUESTIONS.get(top.department)
    else:
        action = "fallback"
        question = None

    return TriageResult(
        query=query, normalised=normalised,
        department=top.department if action != "fallback" else fallback_department,
        confidence=top.score,
        action=action,
        scores=blended,
        matched_terms=terms,
        runner_up=runner_up,
        clarifying_question=question,
    )
