# DxEngine State Schema

## DiagnosticState (state.json)

The master state object for each diagnostic session.

```json
{
  "session_id": "a1b2c3d4e5f6",
  "created_at": "2025-01-15T10:30:00",
  "updated_at": "2025-01-15T10:35:00",
  "patient": { ... PatientProfile ... },
  "problem_representation": { ... ProblemRepresentation ... },
  "hypotheses": [ ... Hypothesis[] ... ],
  "all_evidence": [ ... Evidence[] ... ],
  "lab_analyses": [ ... LabValue[] ... ],
  "pattern_matches": [ ... LabPatternMatch[] ... ],
  "recommended_tests": [ ... RecommendedTest[] ... ],
  "iterations": [ ... LoopIteration[] ... ],
  "current_iteration": 0,
  "max_iterations": 5,
  "converged": false,
  "convergence_reason": "",
  "should_widen_search": false,
  "reasoning_trace": [],
  "errors": []
}
```

## Session Directory Layout

```
state/sessions/{session_id}/
├── state.json                    # Current state
├── state_backup_iter0.json       # Backup before iteration 0
├── state_backup_iter1.json       # Backup before iteration 1
└── ...
```

## State Lifecycle

1. **Created** by intake (Phase 1) - contains patient data and initial lab analysis
2. **Updated** each iteration - new evidence, updated posteriors, pattern matches
3. **Finalized** at convergence or max iterations - final differential
4. **Preserved** for review - session directory persists until manually cleaned
