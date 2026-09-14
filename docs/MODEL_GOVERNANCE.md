# RAMS LLM model-selection governance plan

**Owner:** HIVE AI Council with the RAMS service owner  
**Review cadence:** Monthly and immediately after a provider retirement notice  
**Last OpenRouter check:** 13 September 2026

1. **Keep one governed inventory of every model RAMS may use.**

   HIVE must fetch both `GET https://openrouter.ai/api/v1/models` and the
   account-filtered `GET https://openrouter.ai/api/v1/models/user`. The second response is
   decisive because it reflects the account's provider preferences, privacy settings and
   guardrails. Store the raw responses, retrieval time and HIVE council run ID with the monthly
   decision. Do not copy model names from marketing pages or rely on an unversioned nickname.

   Record the exact `id`, canonical slug, provider, input/output price, cache price, context
   window, structured-output/tool support, availability, measured reliability, data-policy
   compatibility and `expiration_date`. Compare the snapshot with the previous month and flag:

   - removed IDs;
   - a new or changed expiry date;
   - price changes of 10% or more;
   - loss of `response_format`, `structured_outputs`, or required tools;
   - loss of a privacy-compatible provider route; and
   - a material reliability or task-evaluation regression.

   OpenRouter API reference:
   <https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties>.

2. **Use the current OpenRouter check as the baseline, not a permanent allow-list.**

   The 13 September 2026 catalogue check supports this starting route:

   | RAMS role | Baseline model | Current input/output price per 1M tokens | Reason |
   |---|---|---:|---|
   | Triage/classification | `google/gemini-2.5-flash-lite` | $0.10 / $0.40 | Low-cost structured classification |
   | Primary repository work | `openai/gpt-5.6-sol` | $1.00 / $5.00 current API rate | Strong coding result, structured output and substantially cheaper than an expert chair |
   | Independent fallback/reviewer | `anthropic/claude-sonnet-5` | $2.00 / $10.00 | Different provider family, structured output and strong coding/review fit |
   | Expert adjudication only | `anthropic/claude-opus-5` | $5.00 / $25.00 | Reserved for unresolved high-risk ambiguity |

   The live catalogue reported `expiration_date: null` for the four baseline models. Null means
   “no date currently advertised”, not “will never retire”. HIVE must repeat the check monthly.
   OpenRouter marks `anthropic/claude-opus-5-fast` as deprecated from 1 September 2026; RAMS
   rejects that alias. If fast Opus capacity is ever justified, target regular
   `anthropic/claude-opus-5` through OpenRouter's fast service tier.

   Source pages:
   <https://openrouter.ai/openai/gpt-5.6-sol>,
   <https://openrouter.ai/anthropic/claude-sonnet-5>,
   <https://openrouter.ai/anthropic/claude-opus-5>, and
   <https://openrouter.ai/anthropic/claude-opus-5-fast>.

3. **Classify the task before comparing models.**

   Assign one role and risk level to each workload:

   | Complexity | RAMS examples | Route |
   |---|---|---|
   | Low | issue classification, simple extraction, routing | Triage model; tightly capped output |
   | Standard | bounded patch planning, routine code reasoning | Lowest-cost candidate that passes the role evaluation |
   | Complex/high risk | root-cause uncertainty, security or schema implications | Two independent standard reviewers |
   | Expert exception | standard reviewers disagree or cannot reach the confidence gate | Approved expert chair, or manual review when approval is absent |

   Also record whether the task needs exact source context, structured JSON, tools, long context,
   images/files, low latency, or a particular privacy route. A model that lacks a mandatory
   capability is not a candidate, regardless of price.

4. **Qualify performance before optimising cost.**

   Maintain a small, versioned evaluation set for each role using representative RAMS tasks.
   A candidate must pass all safety and contract tests and meet the role's agreed quality floor.
   Record `evaluation_passed`, `task_score`, `reliability_score`, sample size and evidence path in
   the HIVE registry. Never select a cheap model that fails the required outcome; equally, do not
   pay more for a statistically insignificant quality difference.

   For code work, the minimum evaluation covers valid AnchorPatch output, correct root cause,
   bounded files/changes, regression avoidance, security-sensitive rejection and validation
   success. Re-run the evaluation after a model, provider route or prompt changes.

5. **Compare total cost per successful task, not headline token price.**

   HIVE should calculate and submit `cost_per_successful_task` for every qualified role candidate:

   `total request cost, including retries and fallbacks / validated successful tasks`

   Include input, output, reasoning, cache, retry and fallback usage. Use latency and reliability
   as tie-breakers after cost. RAMS chooses the lowest-cost evaluated candidate within the best-fit
   task category, then score and reliability. Where only legacy scores exist, RAMS keeps the old
   score-first behaviour until HIVE supplies cost evidence.

   Spending is an optimisation signal, not a kill switch. RAMS records and reviews cost but does
   not stop authorised workloads merely because a budget target has been reached.

6. **Require this checklist before selecting or changing a model.**

   - [ ] The task has one named role, complexity level and risk level.
   - [ ] The exact model ID appears in the account-filtered OpenRouter catalogue.
   - [ ] Status is active, an eligible provider is available and the data policy is compatible.
   - [ ] No retirement occurs within 30 days and the model is not deprecated.
   - [ ] Required structured-output, tool, modality and context capabilities are present.
   - [ ] The model passed the current role evaluation with traceable evidence.
   - [ ] Cost per successful task was compared with every cheaper qualified candidate.
   - [ ] A different provider/model is available for a genuine fallback where required.
   - [ ] A premium model has a complete, independently approved, unexpired justification.
   - [ ] The decision, rejected alternatives, owner, date and next review are persisted.

   Decision path: low-complexity work goes to triage; standard work goes to the cheapest qualified
   standard model; complex work receives two independent standard reviews. Confident agreement
   completes the council without an expert call. Disagreement or low confidence may reach an
   expert only after the premium gate. Otherwise the item goes to manual review.

   For autonomous code repair, RAMS first runs progressive self-improvement using the governed
   `triage -> secondary -> primary` role ladder (up to three loops by default, hard-capped at four).
   A loop that reaches `RMS_SELF_IMPROVEMENT_CONFIDENCE` bypasses the engineering council but not
   deterministic patch validation or the Phase 4C auto-PR gate. Only an exhausted loop sequence may
   reach the engineering council. Council execution is hard-capped at two meetings per task and a
   second meeting is permitted only after a technical council failure. Positive council approvals
   within `RMS_ENGINEERING_COUNCIL_NEAR_THRESHOLD_TOLERANCE_PERCENT` points of the configured confidence
   threshold are accepted; rejection decisions are never converted into approvals by the tolerance.

7. **Make premium use an exception with a structured approval.**

   GPT-4-class models excluding mini/nano variants, Claude Opus, explicit `expert`/`premium`
   candidates, and any candidate costing more than twice a qualified alternative require approval.
   The requester must provide all of the following before HIVE submits the selection:

   - unique justification ID;
   - accountable owner;
   - specific reason the standard route is insufficient;
   - cheaper model actually tested, which must differ from the selected model;
   - evaluation evidence showing the material failure or benefit;
   - independent approver; and
   - expiry date no more than 90 days ahead.

   Example candidate:

   ```json
   {
     "model_id": "anthropic/claude-opus-5",
     "approved_roles": ["chair"],
     "tier": "expert",
     "evaluation_passed": true,
     "cost_per_successful_task": 0.42,
     "expiration_date": null,
     "supported_parameters": ["response_format", "structured_outputs", "tools"],
     "premium_justification": {
       "justification_id": "rams-expert-2026-09-001",
       "owner": "RAMS service owner",
       "reason": "Standard reviewers missed a confirmed high-risk regression.",
       "cheaper_model_tested": "openai/gpt-5.6-sol",
       "evidence": ["evals/rams-council-2026-09.json"],
       "approved_by": "HIVE AI Council chair",
       "expires_at": "2026-12-01T00:00:00Z"
     }
   }
   ```

   RAMS rejects an incomplete, self-approved, overlong or expired justification before persisting
   or calling the model. Every premium request is counted by model. Approval expiry is checked at
   request time as well as on service start.

8. **Have HIVE submit one complete candidate registry atomically.**

   HIVE groups evaluated candidates under `fast`, `cheap`, `coding`, `reasoning`, `planning`,
   `complex` and `expert`, then calls the authenticated endpoint:

   ```bash
   curl -fsS -X POST "$RAMS_URL/ops/model-governance/apply" \
     -H "Authorization: Bearer $RMS_API_KEY" \
     -H "Content-Type: application/json" \
     --data @rams-model-registry.json
   ```

   The body is `{"sourceRunId":"...","registry":{...}}`. RAMS filters invalid candidates,
   creates role assignments, validates every premium exception, persists the decision to
   `state/model-governance/rams.json`, then swaps the live router. Persistence happens before the
   running configuration changes, so a reported success survives restart. Invalid governance data
   returns HTTP 422; persistence failure returns HTTP 503.

9. **Use retirement controls that leave time to migrate.**

   RAMS will not newly select a model whose status is retired, retiring, deprecated, disabled,
   expired, unavailable or policy-incompatible. It also excludes a model with an invalid expiry or
   one expiring within 30 days. When HIVE finds a retirement:

   1. open a migration record immediately;
   2. evaluate at least two active alternatives;
   3. select and submit the lowest-cost qualified replacement;
   4. run a dry-run RAMS smoke test and compare outputs;
   5. apply the production registry before the 30-day boundary; and
   6. verify that no configuration, persisted assignment or report still references the old ID.

10. **Audit current usage before each monthly council.**

    Export the previous 30 and 90 days of RAMS run reports and aggregate by exact model ID, role,
    pipeline and task class. Review requests, input/output/reasoning/cache tokens, actual cost,
    duration, fallbacks, premium requests, validation success, manual-review rate and cost per
    successful task. Join failures to the selected and fallback models rather than averaging the
    whole service.

    Flag for rationalisation:

    - premium use without a live approval or with weak evidence;
    - a higher-cost model with no measurable quality/reliability gain;
    - duplicate models serving indistinguishable roles;
    - fallbacks used as de facto primaries;
    - models with no calls for 90 days;
    - repeated retries or unusually long outputs; and
    - retired, missing or policy-incompatible model IDs.

11. **Rationalise usage through controlled comparisons.**

    For each flagged route, replay the same evaluation set against the incumbent and at least one
    cheaper active model. Move traffic only when the replacement meets the quality and safety floor.
    Start with dry-run/shadow evidence, then update the HIVE registry. Remove redundant assignments
    after the fallback and rollback paths are confirmed. Record the before/after cost per successful
    task, quality score, reliability and latency so the next council can verify the saving persisted.

12. **Operate clear ownership and evidence retention.**

    HIVE owns catalogue discovery, evaluation evidence and the monthly recommendation. The RAMS
    owner confirms role fit and production behaviour. An authorised council approver signs premium
    exceptions and cannot be the requester. Operations verifies the persisted decision and dry-run
    evidence. Keep decisions and supporting evaluations for at least the same retention period as
    RAMS run reports. Emergency provider outages may use an already-qualified fallback; they do not
    waive premium approval.

13. **Verify the implementation after every governance change.**

    Confirm the apply response contains `schemaVersion: rams-model-governance/v2`, `persisted: true`,
    role decisions, cost evidence and premium approval IDs where applicable. Restart a test instance
    to prove restoration, run one authenticated dry-run pipeline, inspect per-model usage, and test
    that an unapproved premium call fails before any OpenRouter request. The release gate is complete
    only when the model assignment, evaluation evidence and runtime usage all refer to the same HIVE
    council run ID.
