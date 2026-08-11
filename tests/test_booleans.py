"""Tests for the evidence-weighted boolean resolver."""

from __future__ import annotations

import pytest

from hospital_extractor.models.intake import IntakeEvidence
from hospital_extractor.schema.boolean_resolver import (
    NO_PATTERNS,
    YES_PATTERNS,
    apply_detectors,
    detectors,
    resolve,
)
from hospital_extractor.schema.intake import boolean_keys

URL = "https://example.com/"


class TestCorpus:
    def test_corpora_are_non_empty_and_valid(self) -> None:
        assert YES_PATTERNS
        assert NO_PATTERNS
        for pattern, weight in (*YES_PATTERNS, *NO_PATTERNS):
            assert weight > 0
            import re

            re.compile(pattern)

    def test_every_boolean_field_has_a_detector(self) -> None:
        names = {detector.name for detector in detectors()}
        assert names == boolean_keys()

    def test_negated_forms_present(self) -> None:
        assert any("no" in pattern or "without" in pattern for pattern, _ in NO_PATTERNS)


class TestDetectors:
    def test_yes_for_plain_mention(self) -> None:
        items = apply_detectors("We offer heart transplant surgery.", URL)
        matches = [item for item in items if item.field_key == "heart_transplant"]
        assert len(matches) == 1
        assert matches[0].decision == "yes"

    def test_no_for_negated_mention(self) -> None:
        items = apply_detectors("This facility has no da vinci robotic system.", URL)
        matches = [item for item in items if item.field_key == "da_vinci_robotic_surgery"]
        assert any(item.decision == "no" for item in matches)
        intake, _ = resolve({}, {"da_vinci_robotic_surgery": matches})
        assert intake.booleans["da_vinci_robotic_surgery"] == "no"

    def test_no_mention_yields_nothing(self) -> None:
        assert apply_detectors("Plain visitor information page.", URL) == []

    def test_evidence_window_is_bounded(self) -> None:
        text = "word " * 1000
        items = apply_detectors(text + "cardiac", URL)
        assert items
        for item in items:
            assert len(item.text) <= 400


class TestResolve:
    def test_resolves_tristate(self) -> None:
        evidence = {
            "icu": [IntakeEvidence.yes("icu", "ICU beds", URL, method="record")],
            "jci_accredited": [
                IntakeEvidence.no("jci_accredited", "not JCI", URL, method="text", strength=10.0)
            ],
            "heart_transplant": [],
        }
        intake, flat = resolve({}, evidence)
        assert intake.booleans["icu"] == "yes"
        assert intake.booleans["jci_accredited"] == "no"
        assert intake.booleans["heart_transplant"] == "unknown"
        assert flat

    def test_balanced_signals_are_unknown(self) -> None:
        evidence = {
            "cardiac_sciences_enabled": [
                IntakeEvidence.yes("cardiac_sciences_enabled", "cardiac", URL, method="record"),
                IntakeEvidence.no("cardiac_sciences_enabled", "no cardiac", URL, method="text"),
            ]
        }
        intake, _ = resolve({}, evidence)
        assert intake.booleans["cardiac_sciences_enabled"] == "unknown"

    def test_strength_override(self) -> None:
        evidence = {
            "icu": [IntakeEvidence.yes("icu", "ICU", URL, method="record")],
        }
        intake, _ = resolve({}, evidence, strengths={"icu": 0.5})
        assert intake.booleans["icu"] == "yes"

    def test_evidence_for_non_boolean_keys_ignored(self) -> None:
        evidence = {
            "hospital_name": [IntakeEvidence.yes("hospital_name", "X", URL, method="record")]
        }
        intake, _ = resolve({}, evidence)
        assert "hospital_name" not in intake.booleans


class TestDetectorValidation:
    def test_invalid_pattern_raises(self) -> None:
        from hospital_extractor.schema.boolean_resolver import Detector

        with pytest.raises(ValueError):
            Detector(name="broken", yes_patterns=(("([unclosed", 1.0),))
