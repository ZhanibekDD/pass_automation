# Annotation Files for AI Evaluation Metrics

This directory holds manually annotated ground truth used to compute **precision, recall, F1, and coverage** for the AI extraction pipeline.

## Privacy constraints

- `document_id` is the internal `EmployeeDocument.pk` (integer key). **No names, IINs, or document images are stored here.**
- Actual extracted or expected values are never recorded — only boolean judgments (`ai_correct: true/false`).
- Real annotation batches (`batch_*.json`) are **gitignored**. Only `schema.json` and `sample.json` are committed.

## File format

See [`schema.json`](schema.json) for the full JSON schema.

```json
[
  {
    "document_id": "954",
    "document_code": 7,
    "annotator_id": "op1",
    "annotated_at": "2026-07-29",
    "fields": {
      "document_type": { "applicable": true, "present": true, "ai_extracted": true, "ai_correct": true },
      "full_name":     { "applicable": true, "present": true, "ai_extracted": true, "ai_correct": false },
      "iin":           { "applicable": true, "present": true, "ai_extracted": true, "ai_correct": true },
      "issue_date":    { "applicable": true, "present": true, "ai_extracted": true, "ai_correct": true },
      "expiry_date":   { "applicable": false, "present": false, "ai_extracted": false, "ai_correct": null }
    },
    "findings": {
      "fio_mismatch":          { "expected": true,  "detected": false },
      "expired_document":      { "expected": false, "detected": false },
      "unreadable_expiry_date":{ "expected": false, "detected": false }
    }
  }
]
```

## Metric definitions

| Metric | Formula | Notes |
|--------|---------|-------|
| **Precision** | TP / (TP + FP) | Fraction of AI extractions that were correct |
| **Recall** | TP / (TP + FN) | Fraction of present fields that AI extracted correctly |
| **F1** | 2 · P · R / (P + R) | Harmonic mean |
| **Coverage** | extracted / present | Reach: how many present fields did AI extract *at all* (correct or not). **Not called accuracy.** |

Denominators are based on **applicable field slots only** — never on `5 fields × page count`.

## How to annotate

1. Run analysis for a document via the API.
2. Open the document manually and verify each field.
3. For each field: set `applicable` based on document type, `present` based on what the document contains, `ai_extracted` based on whether the AI returned a value, `ai_correct` based on whether the value matches the document.
4. For each relevant finding code: set `expected` (should this finding be raised?) and `detected` (did AI raise it?).
5. Save to `data/annotations/batch_YYYYMMDD.json` — file stays local, never pushed to GitHub.

## Running metrics

```bash
# Via API (admin token required):
curl -s http://localhost:8090/api/ai/metrics -H "X-Api-Key: $AI_TOKEN_ADMIN" | python3 -m json.tool

# Via CLI:
python3 -c "
from pathlib import Path
from app.ai.metrics import load_annotations_dir, calculate_metrics
import json
anns = load_annotations_dir(Path('data/annotations'))
print(json.dumps(calculate_metrics(anns).as_dict(), indent=2))
"
```
