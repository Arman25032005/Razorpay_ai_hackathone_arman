# Agent

## Design choice: explicit state machine, not LangGraph

The agent is a linear, explicit Python state machine
(`app/agents/orchestrator.py`), not built on LangGraph. Functionally this
delivers the same bounded-autonomy guarantee the spec's LangGraph-based
design calls for — no step the reasoning layer can skip past policy
validation — without the added dependency and its own learning curve
inside a 3-day window. If LangGraph's graph-based state management becomes
valuable later (e.g. parallel branches, more complex retry topologies),
migrating the existing explicit states onto it is a contained change,
since the states themselves are already named and explicit (see below).

## Agent state, concretely

`RecoveryCase` (in `app/models.py`) IS the persisted agent state — not a
separate in-memory graph state that then gets serialized. Fields:

```
source_type, source_id, amount_at_risk       payment context
root_cause, root_cause_confidence            diagnosis
recommended_strategy                         decision
current_step                                 live agent step (see below)
attempt_count, status, stop_reason           execution/verification state
human_escalation_required, escalation_reason human-in-the-loop state
amount_recovered, resolved_at                measured outcome
```

`current_step` is the granular live-agent trail: `detected ->
understanding_customer -> diagnosing -> choosing_strategy -> policy_check
-> executing -> waiting_for_result -> recovered|stopped`. Every value is a
real state the case has actually passed through — the UI never displays
"AI is thinking..." without a corresponding state transition having
occurred (per spec section 44's explicit prohibition on faked agentic
behavior).

## Workflow

```
create_case()
  -> analyze_case()
       -> build_customer_context()        real payment/invoice history from the DB
       -> ai_service.diagnose()           structured Decision: root_cause,
                                           confidence, recommended_strategy,
                                           reasoning_summary, escalation flag
       -> _apply_strategy_optimizer()     re-rank among the diagnosis's OWN
                                           candidate strategies by real
                                           historical performance (never
                                           introduces an unvetted strategy)
  -> execute_next_action()  [called in a loop until terminal]
       -> PRE-ACTION VERIFICATION         re-check current payment status;
                                           cancel gracefully if already resolved
                                           (spec section 35)
       -> check_policy()                  deterministic gate — see below
       -> [blocked] -> ESCALATED, human review queue
       -> [allowed] -> execute via provider, observe real result
       -> evaluate_stop_condition()       RECOVERED / STOPPED / ESCALATED
```

## Tools (spec section 18) — mapped to what's actually implemented

| Spec tool | Implementation |
|---|---|
| `get_payment()` | `db.query(Payment).filter(...)` in the orchestrator |
| `get_latest_payment_status()` | `app/payment_state_machine.py` + the pre-action verification step |
| `get_customer_history()` | `build_customer_context()` |
| `get_merchant_policy()` | `app/policies/engine.py::get_active_policy()` |
| `predict_recovery_probability()` | `app/agents/ai_service.py::predict_recovery_probability()` |
| `get_available_recovery_actions()` | `ACTION_TYPE_BY_STRATEGY` mapping in the orchestrator |
| `create_recovery_case()` | `orchestrator.create_case()` |
| `send_notification()` | `app/providers/communication.py` |
| `request_human_review()` | policy-check failure path -> `ESCALATED` status |
| `execute_supported_payment_action()` | `app/providers/payment.py` (Mock or Razorpay) |
| `verify_payment_result()` | the pre-action verification gate, plus post-action result observation |
| `stop_recovery()` | `evaluate_stop_condition()` |

These aren't separate LLM-callable tool functions with a tool-calling
protocol (no LLM is making autonomous tool calls here) — they're plain
Python functions the orchestrator calls in a fixed, explicit order. The
LLM/rule-engine layer (`ai_service.diagnose()`) only ever returns a
structured decision object; it never calls any of these directly. This is
the literal implementation of spec section 18's requirement: "the LLM
cannot bypass policy."

## Structured output validation (spec section 19)

`ai_service.diagnose()` returns a `Decision` dataclass with fixed fields.
For the optional real-LLM path (`_diagnose_via_llm`, active when
`LLM_API_KEY` is set), the raw LLM response is validated against an
explicit allow-list of root causes and strategies
(`ALLOWED_ROOT_CAUSES`, `ALLOWED_STRATEGIES`) before being accepted — any
value outside those sets, or a malformed response, causes the function to
return `None`, which routes the caller back to the deterministic rule
engine. **No unvalidated LLM output ever reaches the action layer.**

## Policy engine (spec section 20)

`app/policies/engine.py::check_policy()` — plain deterministic code,
editable per-deployment via `PUT /api/policies` (persisted, actually
enforced on the next check, not just displayed). Current rules: max
attempts, max workflow age, allowed channels, large-amount human-approval
threshold, discount human-approval requirement. The LLM/rule-engine layer
has no code path that can modify or bypass these.

## Human-in-the-loop (spec section 21)

Case statuses: `OPEN, ANALYZING, ACTION_READY, EXECUTING, RECOVERED,
ESCALATED, STOPPED`. (`FAILED`, `PENDING_REVIEW`/`APPROVED`/`REJECTED` from
the spec's suggested list are folded into `ESCALATED` + the
approve/reject actions, rather than modeled as separate statuses — a
scope simplification, not a missing capability: `POST
/api/recovery-cases/{id}/approve` and `/reject` cover the same behavior.)

## Agent test cases (spec section 34) — status

| Spec test | Covered? | Where |
|---|---|---|
| Temporary failure, high probability -> retry | Yes | curated Scenario A |
| Low probability -> stop | Partial — policy escalates on low-confidence root cause | Scenario G |
| High-value payment -> human review | Yes | Scenario F, `test_large_transaction_requires_approval` |
| Payment already succeeded -> no action | Yes | Scenario H, `test_graceful_cancellation_when_payment_already_captured` |
| Repeated failed retries -> stop | Yes | `test_max_attempts_produces_escalation` |
| Unknown customer -> don't invent history | Yes | `customer_health_score()` returns "unknown" band with 0 history, never fabricates |
| Duplicate webhook -> no duplicate action | Yes | `test_duplicate_webhook_is_idempotent`, `test_razorpay_webhook_end_to_end_idempotent` |
| Out-of-order webhook -> correct final state | Yes | `test_state_machine_rejects_out_of_order_downgrade` |
