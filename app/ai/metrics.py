"""Evaluation metrics for AI extraction quality.

Precision, recall, F1, coverage — calculated per field over a manually-annotated
ground truth dataset stored in data/annotations/.

Key rule: denominators are based on *applicable slots* (fields that are expected
to be present in a given document type), NOT on 5 fields × page count.

Coverage is tracked separately and is NOT labelled as "accuracy" — it measures
how many applicable fields the AI extracted at all, regardless of correctness.

No PII is stored in annotations: document_id is an internal DB key; actual
values (names, IIN, dates) are never written to annotation files.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Annotation data model ───────────────────────────────────────────────────────

EXTRACTABLE_FIELDS = ("document_type", "full_name", "iin", "issue_date", "expiry_date")

FINDING_CODES = (
    "fio_mismatch",
    "iin_mismatch",
    "other_employee_document",
    "expired_document",
    "unreadable_expiry_date",
    "partial_expiry_date",
    "empty_dates",
)


@dataclass(frozen=True)
class FieldAnnotation:
    """Ground truth for one field in one document. No PII stored."""

    applicable: bool        # Is this field expected in this document type?
    present: bool           # Was the value actually in the document?
    ai_extracted: bool      # Did AI produce a non-null value?
    ai_correct: bool | None # True = correct, False = wrong, None = not applicable


@dataclass(frozen=True)
class FindingAnnotation:
    """Ground truth for one finding code in one document."""

    expected: bool    # Should this finding be raised for this document?
    detected: bool    # Did AI raise this finding?


@dataclass
class DocumentAnnotation:
    """Full annotation for one document. No PII stored."""

    document_id: str         # Internal AI DB key only
    document_code: int
    annotator_id: str        # Operator identifier
    annotated_at: str        # ISO date
    fields: dict[str, FieldAnnotation] = field(default_factory=dict)
    findings: dict[str, FindingAnnotation] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentAnnotation:
        fields: dict[str, FieldAnnotation] = {}
        for fname, fdata in data.get("fields", {}).items():
            fields[fname] = FieldAnnotation(
                applicable=fdata["applicable"],
                present=fdata["present"],
                ai_extracted=fdata["ai_extracted"],
                ai_correct=fdata.get("ai_correct"),
            )
        findings: dict[str, FindingAnnotation] = {}
        for code, fdata in data.get("findings", {}).items():
            findings[code] = FindingAnnotation(
                expected=fdata["expected"],
                detected=fdata["detected"],
            )
        return cls(
            document_id=str(data["document_id"]),
            document_code=int(data["document_code"]),
            annotator_id=str(data["annotator_id"]),
            annotated_at=str(data["annotated_at"]),
            fields=fields,
            findings=findings,
        )


# ── Metrics result ──────────────────────────────────────────────────────────────


@dataclass
class FieldMetrics:
    """Precision / recall / F1 for one field across all annotated documents."""

    field_name: str
    applicable_count: int   # slots where field was expected
    present_count: int      # slots where field was actually present in doc
    tp: int                 # extracted and correct
    fp: int                 # extracted but wrong, OR hallucinated (not present)
    fn: int                 # not extracted but should have been
    tn: int                 # not extracted and not expected (or not present)
    extracted_count: int    # total slots where AI produced any value

    @property
    def precision(self) -> float | None:
        denom = self.tp + self.fp
        return self.tp / denom if denom else None

    @property
    def recall(self) -> float | None:
        denom = self.tp + self.fn
        return self.tp / denom if denom else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or (p + r) == 0:
            return None
        return 2 * p * r / (p + r)

    @property
    def coverage(self) -> float | None:
        """Fraction of present slots where AI extracted anything (correct or not).
        Separate from precision — measures extraction *reach*, not correctness.
        Denominator = present_count (fields actually in the document).
        """
        return self.extracted_count / self.present_count if self.present_count else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "applicable_count": self.applicable_count,
            "present_count": self.present_count,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "extracted_count": self.extracted_count,
            "precision": round(self.precision, 4) if self.precision is not None else None,
            "recall": round(self.recall, 4) if self.recall is not None else None,
            "f1": round(self.f1, 4) if self.f1 is not None else None,
            "coverage": round(self.coverage, 4) if self.coverage is not None else None,
        }


@dataclass
class FindingMetrics:
    """Precision / recall / F1 for one finding code."""

    finding_code: str
    tp: int   # correctly detected
    fp: int   # false alarm
    fn: int   # missed
    tn: int   # correctly absent

    @property
    def precision(self) -> float | None:
        denom = self.tp + self.fp
        return self.tp / denom if denom else None

    @property
    def recall(self) -> float | None:
        denom = self.tp + self.fn
        return self.tp / denom if denom else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or (p + r) == 0:
            return None
        return 2 * p * r / (p + r)

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding_code": self.finding_code,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "precision": round(self.precision, 4) if self.precision is not None else None,
            "recall": round(self.recall, 4) if self.recall is not None else None,
            "f1": round(self.f1, 4) if self.f1 is not None else None,
        }


@dataclass
class EvaluationReport:
    annotation_count: int
    field_metrics: dict[str, FieldMetrics]
    finding_metrics: dict[str, FindingMetrics]

    def as_dict(self) -> dict[str, Any]:
        return {
            "annotation_count": self.annotation_count,
            "fields": {k: v.as_dict() for k, v in self.field_metrics.items()},
            "findings": {k: v.as_dict() for k, v in self.finding_metrics.items()},
        }


# ── Core calculation ────────────────────────────────────────────────────────────


def calculate_metrics(annotations: list[DocumentAnnotation]) -> EvaluationReport:
    """Compute precision / recall / F1 / coverage from annotated documents.

    Denominators are based on applicable field slots, never on total pages.
    """
    # Accumulate per-field counters
    field_acc: dict[str, dict[str, int]] = {
        fname: {"applicable": 0, "present": 0, "tp": 0, "fp": 0, "fn": 0, "tn": 0, "extracted": 0}
        for fname in EXTRACTABLE_FIELDS
    }
    finding_acc: dict[str, dict[str, int]] = {
        code: {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        for code in FINDING_CODES
    }

    for ann in annotations:
        for fname, fa in ann.fields.items():
            if fname not in field_acc:
                continue
            acc = field_acc[fname]
            if not fa.applicable:
                continue  # skip non-applicable slots entirely

            acc["applicable"] += 1
            if fa.present:
                acc["present"] += 1
            if fa.ai_extracted:
                acc["extracted"] += 1

            if fa.present and fa.ai_correct:
                acc["tp"] += 1
            elif fa.present and fa.ai_extracted and fa.ai_correct is False:
                acc["fp"] += 1
            elif fa.present and not fa.ai_extracted:
                acc["fn"] += 1
            elif not fa.present and not fa.ai_extracted:
                acc["tn"] += 1
            elif not fa.present and fa.ai_extracted:
                # extracted where field is absent (e.g. hallucinated)
                acc["fp"] += 1

        for code, finding in ann.findings.items():
            if code not in finding_acc:
                continue
            acc = finding_acc[code]
            if finding.expected and finding.detected:
                acc["tp"] += 1
            elif not finding.expected and finding.detected:
                acc["fp"] += 1
            elif finding.expected and not finding.detected:
                acc["fn"] += 1
            else:
                acc["tn"] += 1

    field_metrics: dict[str, FieldMetrics] = {}
    for fname, acc in field_acc.items():
        field_metrics[fname] = FieldMetrics(
            field_name=fname,
            applicable_count=acc["applicable"],
            present_count=acc["present"],
            tp=acc["tp"],
            fp=acc["fp"],
            fn=acc["fn"],
            tn=acc["tn"],
            extracted_count=acc["extracted"],
        )

    finding_metrics: dict[str, FindingMetrics] = {}
    for code, acc in finding_acc.items():
        finding_metrics[code] = FindingMetrics(
            finding_code=code,
            tp=acc["tp"],
            fp=acc["fp"],
            fn=acc["fn"],
            tn=acc["tn"],
        )

    return EvaluationReport(
        annotation_count=len(annotations),
        field_metrics=field_metrics,
        finding_metrics=finding_metrics,
    )


# ── I/O helpers ─────────────────────────────────────────────────────────────────


def load_annotations(path: Path) -> list[DocumentAnnotation]:
    """Load annotations from a JSON file. Returns empty list if file absent."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    return [DocumentAnnotation.from_dict(item) for item in raw]


def load_annotations_dir(directory: Path) -> list[DocumentAnnotation]:
    """Load and merge all *.json annotation files from a directory."""
    all_annotations: list[DocumentAnnotation] = []
    for p in sorted(directory.glob("*.json")):
        if p.name.startswith("_") or p.name == "sample.json":
            continue  # skip schema/sample files
        all_annotations.extend(load_annotations(p))
    return all_annotations
