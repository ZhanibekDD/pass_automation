"""Tests for app.ai.metrics — precision, recall, F1, coverage.

All test annotations are synthetic — no real document IDs, names, or IIN values.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.ai.metrics import (
    DocumentAnnotation,
    FieldAnnotation,
    FindingAnnotation,
    calculate_metrics,
    load_annotations,
    load_annotations_dir,
)

# ── helpers ────────────────────────────────────────────────────────────────────


def make_ann(
    doc_id: str = "X",
    doc_code: int = 6,
    fields: dict | None = None,
    findings: dict | None = None,
) -> DocumentAnnotation:
    fa_dict: dict[str, FieldAnnotation] = {}
    for fname, fdata in (fields or {}).items():
        fa_dict[fname] = FieldAnnotation(**fdata)
    fin_dict: dict[str, FindingAnnotation] = {}
    for code, fdata in (findings or {}).items():
        fin_dict[code] = FindingAnnotation(**fdata)
    return DocumentAnnotation(
        document_id=doc_id,
        document_code=doc_code,
        annotator_id="test_op",
        annotated_at="2026-07-01",
        fields=fa_dict,
        findings=fin_dict,
    )


def _full_name(*, applicable=True, present=True, ai_extracted=True, ai_correct=True):
    return {"full_name": dict(
        applicable=applicable, present=present,
        ai_extracted=ai_extracted, ai_correct=ai_correct
    )}


# ── calculate_metrics: field precision/recall ──────────────────────────────────


def test_perfect_extraction_gives_precision_1():
    anns = [
        make_ann("A", fields=_full_name(ai_correct=True)),
        make_ann("B", fields=_full_name(ai_correct=True)),
    ]
    report = calculate_metrics(anns)
    m = report.field_metrics["full_name"]
    assert m.precision == 1.0
    assert m.recall == 1.0
    assert m.f1 == 1.0


def test_all_wrong_extractions_gives_precision_0():
    anns = [
        make_ann("A", fields=_full_name(ai_correct=False)),
        make_ann("B", fields=_full_name(ai_correct=False)),
    ]
    report = calculate_metrics(anns)
    m = report.field_metrics["full_name"]
    assert m.precision == 0.0
    assert m.recall == 0.0
    assert m.tp == 0
    assert m.fp == 2
    assert m.fn == 2


def test_wrong_extraction_reduces_recall():
    anns = [
        make_ann("A", fields=_full_name(ai_correct=True)),
        make_ann("B", fields=_full_name(ai_correct=False)),
    ]
    report = calculate_metrics(anns)
    m = report.field_metrics["full_name"]
    assert m.tp == 1
    assert m.fp == 1
    assert m.fn == 1
    assert m.recall == 0.5


def test_missed_fields_give_fn_and_zero_recall():
    anns = [
        make_ann("A", fields=_full_name(ai_extracted=False, ai_correct=None)),
        make_ann("B", fields=_full_name(ai_extracted=False, ai_correct=None)),
    ]
    report = calculate_metrics(anns)
    m = report.field_metrics["full_name"]
    assert m.fn == 2
    assert m.tp == 0
    assert m.recall == 0.0


def test_non_applicable_slots_excluded_from_denominator():
    """If field is not applicable (e.g. expiry_date for document_code=33),
    it must NOT count toward tp/fp/fn/tn."""
    anns = [
        make_ann("A", fields={
            "expiry_date": dict(applicable=False, present=False,
                                ai_extracted=False, ai_correct=None)
        }),
        make_ann("B", fields={
            "expiry_date": dict(applicable=True, present=True,
                                ai_extracted=True, ai_correct=True)
        }),
    ]
    report = calculate_metrics(anns)
    m = report.field_metrics["expiry_date"]
    assert m.applicable_count == 1  # only document B counts
    assert m.tp == 1
    assert m.precision == 1.0


def test_hallucinated_field_counts_as_fp_without_inflating_coverage():
    """An absent-field extraction is an FP but not coverage."""
    anns = [
        make_ann("A", fields={
            "expiry_date": dict(applicable=True, present=True,
                                ai_extracted=True, ai_correct=True)
        }),
        make_ann("B", fields={
            "expiry_date": dict(applicable=True, present=False,
                                ai_extracted=True, ai_correct=False)
        }),
    ]
    report = calculate_metrics(anns)
    m = report.field_metrics["expiry_date"]
    assert m.tp == 1
    assert m.fp == 1
    assert m.present_count == 1
    assert m.extracted_count == 1
    assert m.coverage == 1.0


def test_f1_is_harmonic_mean():
    # Wrong extraction contributes FP+FN; missed extraction contributes FN.
    anns = [
        make_ann("A", fields=_full_name(ai_correct=True)),   # TP
        make_ann("B", fields=_full_name(ai_correct=True)),   # TP
        make_ann("C", fields=_full_name(ai_correct=False)),  # FP
        make_ann("D", fields=_full_name(ai_extracted=False, ai_correct=None)),  # FN
    ]
    report = calculate_metrics(anns)
    m = report.field_metrics["full_name"]
    assert m.tp == 2
    assert m.fp == 1
    assert m.fn == 2
    assert round(m.precision, 4) == round(2 / 3, 4)
    assert round(m.recall, 4) == round(1 / 2, 4)
    assert round(m.f1, 4) == round(4 / 7, 4)


# ── coverage is separate from precision ───────────────────────────────────────


def test_coverage_counts_all_extractions_not_just_correct():
    """Coverage = extracted_count / present_count, regardless of correctness.

    All 3 documents have the field present, so present_count = 3.
    AI extracted on A and B (2 docs), so coverage = 2/3.
    Precision is 1/2 (only A was correct among 2 extractions).
    """
    anns = [
        make_ann("A", fields=_full_name(ai_correct=True)),                      # TP
        make_ann("B", fields=_full_name(ai_correct=False)),                     # FP (present+wrong)
        make_ann("C", fields=_full_name(ai_extracted=False, ai_correct=None)),  # FN
    ]
    report = calculate_metrics(anns)
    m = report.field_metrics["full_name"]
    assert m.present_count == 3
    assert m.extracted_count == 2
    assert round(m.coverage, 4) == round(2 / 3, 4)
    # precision: 1 TP out of 2 total extractions (1 TP + 1 FP)
    assert round(m.precision, 4) == round(1 / 2, 4)


def test_coverage_is_not_labelled_accuracy():
    """Verify coverage and precision are not the same number when there are FPs."""
    anns = [
        make_ann("A", fields=_full_name(ai_correct=True)),
        make_ann("B", fields=_full_name(ai_correct=False)),
    ]
    report = calculate_metrics(anns)
    m = report.field_metrics["full_name"]
    assert m.coverage != m.precision


# ── finding metrics ────────────────────────────────────────────────────────────


def test_finding_tp_fp_fn_tn():
    anns = [
        make_ann("A", findings={"fio_mismatch": dict(expected=True, detected=True)}),   # TP
        make_ann("B", findings={"fio_mismatch": dict(expected=False, detected=True)}),  # FP
        make_ann("C", findings={"fio_mismatch": dict(expected=True, detected=False)}),  # FN
        make_ann("D", findings={"fio_mismatch": dict(expected=False, detected=False)}), # TN
    ]
    report = calculate_metrics(anns)
    m = report.finding_metrics["fio_mismatch"]
    assert m.tp == 1
    assert m.fp == 1
    assert m.fn == 1
    assert m.tn == 1
    assert round(m.precision, 4) == 0.5
    assert round(m.recall, 4) == 0.5


def test_finding_precision_none_when_no_detections():
    anns = [make_ann("A", findings={"fio_mismatch": dict(expected=False, detected=False)})]
    report = calculate_metrics(anns)
    m = report.finding_metrics["fio_mismatch"]
    assert m.precision is None  # denom = TP+FP = 0


# ── empty annotations ──────────────────────────────────────────────────────────


def test_empty_annotations_returns_zero_counts():
    report = calculate_metrics([])
    assert report.annotation_count == 0
    for m in report.field_metrics.values():
        assert m.tp == m.fp == m.fn == 0
        assert m.precision is None
        assert m.recall is None


# ── I/O: load from JSON ────────────────────────────────────────────────────────


def test_load_annotations_from_file():
    data = [
        {
            "document_id": "T1",
            "document_code": 6,
            "annotator_id": "op1",
            "annotated_at": "2026-07-01",
            "fields": {
                "full_name": {
                    "applicable": True, "present": True,
                    "ai_extracted": True, "ai_correct": True
                }
            },
            "findings": {}
        }
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                     delete=False, encoding="utf-8") as f:
        json.dump(data, f)
        tmp_path = Path(f.name)

    try:
        anns = load_annotations(tmp_path)
        assert len(anns) == 1
        assert anns[0].document_id == "T1"
        assert anns[0].fields["full_name"].ai_correct is True
    finally:
        tmp_path.unlink(missing_ok=True)


def test_load_annotations_dir_skips_metadata_files():
    """sample.json and schema.json must not be loaded as annotation batches."""
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        sample = [{"document_id": "SAMPLE-001", "document_code": 6,
                   "annotator_id": "op1", "annotated_at": "2026-07-01",
                   "fields": {}, "findings": {}}]
        (d / "sample.json").write_text(json.dumps(sample))
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                  "type": "array"}
        (d / "schema.json").write_text(json.dumps(schema))
        real = [{"document_id": "REAL-001", "document_code": 7,
                 "annotator_id": "op1", "annotated_at": "2026-07-01",
                 "fields": {}, "findings": {}}]
        (d / "batch_001.json").write_text(json.dumps(real))
        anns = load_annotations_dir(d)
    assert len(anns) == 1
    assert anns[0].document_id == "REAL-001"


def test_load_annotations_missing_file_returns_empty():
    anns = load_annotations(Path("/nonexistent/path.json"))
    assert anns == []


# ── as_dict serialization ──────────────────────────────────────────────────────


def test_report_as_dict_contains_expected_keys():
    anns = [make_ann("A", fields=_full_name())]
    report = calculate_metrics(anns)
    d = report.as_dict()
    assert "annotation_count" in d
    assert "fields" in d
    assert "findings" in d
    assert "full_name" in d["fields"]
    field_d = d["fields"]["full_name"]
    for key in ("tp", "fp", "fn", "tn", "precision", "recall", "f1", "coverage",
                "applicable_count", "extracted_count"):
        assert key in field_d, f"missing key: {key}"
