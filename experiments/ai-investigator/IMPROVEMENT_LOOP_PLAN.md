# Phoenix improvement loop for the drift investigator

> **Status:** implementation plan. This document does not authorize automated changes to a
> monitored model, its data, its thresholds, or its deployment.
>
> **Depends on:** the investigator contract in `.claude/agents/drift-investigator.md` and the
> validation design in `experiments/ai-investigator/PLAN.md`. The contract wins if they disagree.

## Decision

Build two linked loops with different owners and permissions:

1. **Investigator-quality loop:** use Phoenix traces, evaluations, datasets, experiments, and
   human annotations to improve the investigator's prompt, tools, validators, and model choice.
2. **Model-remediation loop:** translate a validated investigation into an allowlisted action
   proposal for the monitored ML system, route it to an accountable owner, and measure whether
   the approved change achieved its stated outcome.

The first loop can promote an investigator change after its evaluation gates pass. The second
cannot retrain, redeploy, roll back, change a threshold, or mutate production data. It creates an
evidence-linked proposal and records the disposition of work performed by the system that already
owns the model lifecycle.

This separation matters. A better explanation of drift is not proof that the monitored model must
change, and an apparent post-change recovery is not proof that the proposed cause was correct.

Set before implementation:

- Investigator DRI: TBD
- Evaluation DRI: TBD
- ML platform integration DRI: TBD
- Model owner for each enrolled model: TBD
- Human-review DRI: TBD
- Phoenix retention and access policy: TBD
- Cost and latency budgets: TBD

No production or shadow deployment starts while a required field is TBD.

---

## 1. Intended outcome

The loop should make one decision easier and safer: **given a persisted ModelSentry drift event,
what evidence-backed work should happen next, who owns it, and did it help?**

The completed system produces:

- a trace of how the investigator used bounded aggregate evidence;
- deterministic and reviewed quality scores attached to that trace;
- a failure classification when the investigation is weak;
- an experiment comparing a proposed investigator revision with the current version;
- at most one active, typed action proposal for an eligible investigation;
- an approval and execution record owned by the responsible model or data team; and
- a verification result that closes, reopens, or requests rollback review for the action.

It does not produce autonomous root-cause claims or self-directed production changes.

---

## 2. End-to-end code path

```text
persisted drift report + bound profile history
    -> deterministic evidence builder
    -> drift investigator
    -> candidate/result schema validation
    -> runtime ranking and evidence checks
    -> Phoenix trace + code/LLM evaluations + human annotation
    -> deterministic failure classifier
       |-> investigator defect -> curated dataset -> candidate revision -> Phoenix experiment
       |                         -> promote or reject investigator version
       |
       `-> eligible model finding -> deterministic action mapper -> ActionProposal
                                  -> risk gate -> owner approval
                                  -> external diagnostic/model workflow
                                  -> post-change ModelSentry verification
                                  -> close, reopen, or request rollback review
```

### Runtime components

| Component | Reads | Writes | Responsibility |
|---|---|---|---|
| Evidence builder | Bound ModelSentry reports and profiles | Typed aggregate evidence | Own every calculation and identifier binding |
| Investigator | Typed evidence through the contracted tools | Candidate hypotheses and allowlisted checks | Explain observations, uncertainty, and useful next checks |
| Validator/ranker | Candidate plus retrieved evidence | Validated investigation result | Reject invalid citations; compute strength and rank |
| Phoenix exporter | Allowlisted span attributes | Traces and evaluation annotations | Make behavior measurable without exporting raw records |
| Failure classifier | Evaluations and human labels | Fixed failure codes | Route a failure to the correct improvement owner |
| Action mapper | Valid investigation result | `ActionProposal` | Convert a check code to a bounded workflow request |
| Approval adapter | Proposal and owner decision | Immutable status event | Keep consequential actions under accountable control |
| Verification worker | Completed action plus later reports | `ActionOutcome` | Test pre-registered success and rollback conditions |

The action mapper is deterministic. The language model cannot invent action codes, owners,
permissions, commands, endpoints, or success thresholds.

---

## 3. Phoenix trace contract

Use the OpenTelemetry provider registered for Phoenix as specified in the validation plan. Do not
install a second global provider. One investigation is one trace with these spans:

| Span | Purpose | Allowed attributes |
|---|---|---|
| `modelsentry.investigation` | Root lifecycle | keyed correlation ID, contract version, agent version, status, duration |
| `modelsentry.evidence.build` | Deterministic preprocessing | evidence schema version, feature/window counts, status enums |
| `modelsentry.agent.run` | Model interaction | provider/model identifier, prompt version, turns, tokens, latency, status |
| `modelsentry.tool.<name>` | Contracted read-only tool call | tool name, call index, status enum, result count, duration |
| `modelsentry.candidate.validate` | Schema and grounding validation | validator version, failure codes, cited/valid evidence counts |
| `modelsentry.result.rank` | Runtime-owned ranking | ranking version, retained hypothesis count, status |
| `modelsentry.action.map` | Proposal generation | mapper version, action code, risk class, eligibility result |
| `modelsentry.action.review` | Human/system disposition | proposal ID, owner role, decision enum, reason code, duration |
| `modelsentry.action.verify` | Outcome measurement | outcome enum, metric-spec version, elapsed windows |

Never export raw rows, feature values tied to an individual, prompts, completions, full tool
arguments/results, secrets, file paths, exception text, or unhashed model/report identifiers.
Strings retrieved from stored data are untrusted values. They cannot modify instructions, tools,
evaluation rules, action permissions, or stopping conditions.

Phoenix is the observation and evaluation record for the agent path. The ModelSentry store remains
authoritative for drift evidence; the model registry, data platform, and deployment system remain
authoritative for model changes.

---

## 4. Investigator-quality loop

### 4.1 Evaluate every eligible run

Attach the deterministic evaluators already defined in `PLAN.md` to the root or validation span.
Use LLM judges only for rubric-based qualities that deterministic code cannot establish, such as
clarity of uncertainty and usefulness of a proposed check. Treat judge output as screening signal,
not ground truth. Calibrate it against blinded human review.

Classify failures into a fixed taxonomy:

- `schema_failure`
- `invalid_evidence_reference`
- `observation_mismatch`
- `unsupported_causal_claim`
- `missed_plausible_hypothesis`
- `bad_abstention`
- `wrong_next_check`
- `tool_failure`
- `privacy_or_injection_violation`
- `latency_or_cost_budget_exceeded`
- `action_not_executable`
- `post_change_no_improvement`

One run may have several failure codes. Failure classification is code-owned when a deterministic
evaluator can decide it; human reviewers adjudicate ambiguity.

### 4.2 Curate, do not self-train

Add a run to the proposed Phoenix dataset `drift-investigator-regressions` only when it has:

- a valid evidence snapshot reference;
- an investigator, contract, validator, and evaluator version;
- a human-reviewed expected behavior or deterministic expected output;
- the failure code that made the example useful; and
- no disallowed content.

Do not treat the investigator's own hypothesis, an LLM judge score, action approval, or a coincident
metric recovery as ground truth. Keep stable controls, ambiguous cases, left-censored onset,
corrupted history, incompatible bins, tool failures, and injection fixtures in the dataset.

### 4.3 Change one controlled surface

Every candidate revision declares exactly one primary change class:

- prompt wording or examples;
- tool response shape or retrieval policy;
- deterministic evidence calculation;
- validator or ranking rule; or
- model/provider configuration.

If a change must cross classes, split the experiment or document why attribution is impossible.
Version the prompt, tool contract, evidence schema, validator, evaluator set, and model independently.

### 4.4 Compare on identical examples

Run the current and candidate versions as paired Phoenix experiments against the same frozen dataset
version, with identical seeds and budgets where the provider supports them. Development examples may
guide iteration; the holdout remains sealed until the change and gates are frozen.

Phoenix is used here because it can keep test examples in datasets, apply repeatable evaluators,
record experiment runs as traces, and compare prompt/model/application revisions on the same inputs.

### 4.5 Promotion math

For a run set `R`, calculate:

```text
rate(metric) = passing eligible runs / eligible runs
unsupported_claim_rate = unsupported-claim runs / causality-eligible runs
regression_rate = baseline-pass/candidate-fail pairs / baseline-pass pairs
paired_delta(metric) = candidate score - baseline score for each shared example
cost_per_accepted_result = total inference cost / human-accepted results
```

Use a seeded paired bootstrap over examples for a 95% confidence interval on mean paired deltas.
Resample examples, not individual evaluator scores. Publish the seed, number of resamples, point
estimate, interval, numerator, denominator, and null count. The implementation must set the bootstrap
count before the holdout run; proposed default: 10,000 resamples with seed `1255`.

A candidate may be promoted only if all hard gates pass:

- schema validity: 100%;
- evidence-reference validity: 100%;
- observation fidelity: 100%;
- unsupported causal claim rate: 0%;
- privacy or injection violations: 0;
- stable-control false-action proposals: 0;
- no regression in any hard gate;
- overall regression rate on previously passing cases: at most 2%;
- the primary target metric improves and its 95% paired interval excludes zero; and
- median latency and cost remain within the pre-registered budgets.

If the sample is too small for a useful interval, the result is `insufficient_evidence`, not a pass.
Promotion records the dataset, experiment, evaluator, prompt, agent, and contract versions and keeps
the prior version available for rollback.

---

## 5. Model-remediation loop

### 5.1 Eligibility

An investigation can enter action mapping only when:

- both schemas validate;
- assessment is `drift_detected`, not `stable` or `insufficient_evidence`;
- every cited observation passes evidence-reference and fidelity checks;
- unsupported causal claim and privacy/injection evaluators pass;
- at least one retained hypothesis has an allowlisted `check_code`; and
- no active proposal already exists for the same report and mapper version.

Eligibility does not mean the hypothesis is true. It means the proposed diagnostic is safe and
specific enough for review.

### 5.2 Two action tiers

Tier 1 actions are read-only diagnostics derived one-to-one from the contract:

| Investigator `check_code` | Action code | Intended outcome |
|---|---|---|
| `review_upstream_schema` | `inspect_upstream_schema` | Confirm or reject a schema/data-contract change |
| `compare_population_segments` | `compare_population_segments` | Locate whether drift is concentrated in an approved segment |
| `verify_missingness_pipeline` | `inspect_missingness_pipeline` | Confirm or reject a collection/transformation failure |
| `review_deployment_change` | `inspect_deployment_change` | Compare drift timing with an authenticated release record |
| `compare_scheduled_window` | `compare_scheduled_window` | Test a seasonal or scheduled explanation against a defined window |

Tier 2 actions are consequential workflow requests. They may be created only after a Tier 1 result or
equivalent owner-supplied evidence is attached:

- `open_data_quality_ticket`
- `request_retraining_experiment`
- `request_threshold_review`
- `request_rollback_review`
- `close_without_change`

Tier 2 is deliberately phrased as a request. Execution belongs to the existing data, training,
registry, and deployment systems.

### 5.3 Action proposal contract

Define a JSON Schema before implementing adapters. At minimum, `ActionProposal` contains:

```text
proposal_id                 opaque, immutable
source_investigation_id     opaque, immutable
source_trace_id             opaque Phoenix correlation
source_report_id            opaque ModelSentry binding
model_ref                    opaque authenticated scope
action_code                 closed enum
risk_class                  read_only | model_change | production_change
rationale_evidence_ids      1..5 identifiers from the validated result
required_inputs             closed enum list, never credentials or paths
owner_role                  data_owner | model_owner | ml_platform | release_owner
approval_state              proposed | approved | rejected | expired | cancelled
success_metric_spec         versioned metric names, direction, threshold, windows
rollback_condition          required for a production change
expires_at                  bounded review period
contract_versions           agent, evidence, mapper, evaluator
idempotency_key             report + action code + mapper version
```

Free text is explanatory only. No consumer may turn free text into a command. Adapters dispatch from
the action enum and authenticated configuration, never from agent-provided URLs, paths, owners, or
arguments.

### 5.4 Approval policy

- Read-only diagnostics require an enrolled model, authenticated scope, bounded query, and recorded
  owner. Whether they auto-run is a per-model policy set outside the agent.
- Retraining experiments require model-owner approval.
- Data corrections require data-owner approval.
- Threshold changes require model-owner and monitoring-owner approval.
- Rollback or deployment requests require release-owner approval and a rollback condition.
- Any missing owner, expired proposal, conflicting active proposal, invalid version, or unavailable
  evidence stops execution.

The agent never receives write credentials. Rejection is a valid outcome and is not training signal
unless the reviewer supplies a reason code.

### 5.5 Verification

Before approval, the model owner defines the monitored metrics, direction, minimum effect, comparison
window, guardrails, and rollback condition. There is no universal improvement formula for all models.

After an external workflow reports completion, the verification worker waits for the configured
number of complete ModelSentry windows, then computes:

```text
metric_delta = post_change_metric - pre_change_metric
relative_delta = metric_delta / abs(pre_change_metric)  # null when pre-change is zero
drift_recurrence_rate = drifted verification windows / eligible verification windows
action_success_rate = successful completed actions / completed actions with sufficient evidence
```

An action is `successful` only when the pre-registered primary metric meets its threshold and every
guardrail passes. It is `failed` when a rollback condition fires or the primary threshold is missed
with sufficient evidence. It is `inconclusive` when coverage is inadequate, values are unavailable,
or the comparison is not valid. Inconclusive is never coerced to success.

The result may close the proposal, reopen investigation, or create a rollback-review request. It may
not perform the rollback. Report correlation, not causation: the result establishes that the defined
metrics changed after the action under the specified comparison, not that the original hypothesis was
the unique cause.

---

## 6. Feedback rules

Outcomes feed the loops differently:

| Observation | Destination | Permitted response |
|---|---|---|
| Invalid or unsupported investigation | Investigator-quality loop | Add reviewed regression case; revise one controlled surface |
| Correct investigation, wrong/unhelpful check | Both loops | Review check mapping and investigator guidance separately |
| Useful check, no model change needed | Model-remediation loop | Close with reason; do not penalize the investigator |
| Approved change improves pre-registered metrics | Model-remediation evidence | Record success; do not label the hypothesis as proven cause |
| Approved change fails or regresses a guardrail | Both loops | Request rollback review; add reviewed outcome case |
| Tool, adapter, or external workflow failure | Owning platform | Record operational failure; do not score it as model reasoning failure |

Never optimize directly for action-approval rate. That would reward confident or excessive proposals.
Track it for operations, alongside rejection reasons, but promote investigator versions on grounded
quality, safety, usefulness, cost, and latency.

---

## 7. Delivery phases

### Phase 0 — prerequisites

- Persist and validate report/profile/baseline bindings.
- Merge and verify the automatic drift and storage-integrity fixes on which reliable evidence depends.
- Freeze the investigator contract, schemas, and Sonnet 5 configuration for the first baseline.
- Decide Phoenix retention, access, redaction, and deletion policy.

**Exit:** one fixture can be reconstructed by identifiers without ordering or mtime assumptions.

### Phase 1 — observable investigator

- Add the span hierarchy and allowlisted attributes.
- Implement deterministic evaluators and evaluation annotations.
- Add blinded human annotation configs and reviewer guidance.
- Publish dashboards by agent, prompt, contract, and evaluator version.

**Exit:** every validation scenario produces a trace with no prohibited payload and deterministic
scores can be recomputed from fixtures.

### Phase 2 — offline improvement loop

- Create the reviewed regression dataset.
- Build paired baseline/candidate experiments.
- Implement promotion calculations and an immutable promotion record.
- Exercise rollback to the prior investigator version.

**Exit:** one seeded defect is found, revised, compared, promoted or rejected by the gates, and fully
reproduced from recorded versions.

### Phase 3 — action proposals in shadow mode

- Define `ActionProposal` and `ActionOutcome` schemas.
- Implement deterministic eligibility, mapping, idempotency, expiry, and audit events.
- Generate proposals without dispatching them.
- Have model/data owners review usefulness and ownership accuracy.

**Exit:** zero duplicate or unauthorized proposals; 100% evidence binding; owners agree the proposal
contains enough information to decide without opening raw agent content.

### Phase 4 — approved diagnostics

- Connect only Tier 1 actions to bounded, read-only adapters.
- Require explicit per-model enrollment and owner policy.
- Capture adapter status separately from investigator quality.

**Exit:** diagnostic requests are authorized, bounded, idempotent, auditable, and safe under replay.

### Phase 5 — model-lifecycle requests and verification

- Connect Tier 2 proposals to the existing ticket, experiment, registry, or deployment workflow.
- Require the approvals in section 5.4.
- Implement post-change verification and rollback-review creation.

**Exit:** a staged model change travels from drift event to approved external work to a measured,
auditable outcome without giving the investigator production mutation authority.

---

## 8. Required tests

Unit tests:

- span attribute allowlist and redaction;
- correlation identifier generation;
- evaluator numerators, denominators, nulls, and failure codes;
- seeded paired-bootstrap reproducibility;
- every promotion gate boundary;
- check-code-to-action mapping exhaustiveness;
- proposal schema, idempotency, expiry, and version mismatch;
- relative delta with zero baseline;
- success, failure, and inconclusive verification branches.

Integration tests:

- one trace links its evaluations without exporting prohibited content;
- the same frozen examples run through baseline and candidate experiments;
- an invalid investigation cannot create a proposal;
- replay creates no duplicate proposal or action;
- untrusted stored strings cannot alter action code or adapter arguments;
- a Tier 2 request cannot run without its required owner approval;
- an adapter failure is not counted as an investigator reasoning failure;
- a completed external action waits for full verification windows;
- a guardrail regression creates a review request but performs no rollback.

Adversarial tests:

- prompt injection in every stored string field;
- forged action codes, model references, owners, paths, and URLs;
- stale approvals and swapped model/report identifiers;
- conflicting proposals and concurrent retries;
- NaN, Infinity, oversized numbers, duplicate evidence IDs, and corrupted histories;
- traces inspected to confirm raw prompt, completion, evidence payload, and identifiers are absent.

---

## 9. Acceptance criteria

The plan is implemented only when:

- Phoenix shows the complete bounded agent path and its evaluation annotations;
- a reviewed production-like failure can become a versioned dataset example;
- a candidate investigator revision is compared with the current version on identical examples;
- promotion is deterministic, reproducible, and blocked by every safety regression;
- only eligible, grounded investigations create proposals;
- every proposal names an accountable role, success criteria, expiry, and source evidence;
- no language-model output can directly invoke a consequential action;
- retraining, data changes, threshold changes, rollback, and deployment require external ownership and
  approval;
- post-change verification distinguishes success, failure, and insufficient evidence; and
- the complete path is replay-safe, auditable, and contains no prohibited trace payload.

---

## 10. Phoenix references

- [Phoenix overview](https://arize.com/docs/phoenix)
- [Datasets and experiments](https://arize.com/docs/phoenix/datasets-and-experiments/overview-datasets)
- [Using evaluators in experiments](https://arize.com/docs/phoenix/datasets-and-experiments/how-to-experiments/using-evaluators)
- [Annotations and evaluations](https://arize.com/docs/phoenix/tracing/tutorial/annotations-and-evaluations)
- [Prompt optimization techniques](https://arize.com/docs/phoenix/cookbook/prompt-engineering/prompt-optimization)

These references justify the Phoenix mechanics. ModelSentry's contracts, gates, permissions, and
outcome definitions remain repository-owned decisions.
