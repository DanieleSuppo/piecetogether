# Semantic decision adapters: development direction

**Status:** Approved direction for future tickets; production adoption of Jev
depends on the focused evaluation below. No change to implemented #13–#15.

**Authority:** [SPEC #12](https://github.com/DanieleSuppo/piecetogether/issues/12),
with implementation responsibilities in #16, #17, #27, #29 and #31.
**Evidence:** [TypeSafe research](../../research/2026-10-05-typesafe.md), including
primary sources and the distinction between documented capabilities and claims.

## Decision and impact on existing work

Use narrow semantic decision models for bounded prediction tasks where they
can replace a generative LLM call. Evaluate TypeSafe/Jev first as the semantic
selector in Context Assembly (#16), then as a selector of known Entity,
Context and concept candidates. Keep generation for open interpretation,
new descriptions, conversational responses and complex unresolved cases.

The current `ModelProvider.propose` produces a complete `SemanticProposal`,
including a draft response. Its development adapter is deterministic;
there is no production LLM call to replace now. Jev's `Choice`, `Score` and
`Noul` outputs cannot directly implement that complete interface. Future
ModelProvider implementations may compose bounded decisions and generation
internally, then return the same kind of versioned Proposal.

The Entity/Context/Claim records, proposal operation vocabulary, Domain
Contract validator, Grounding evidence, provenance, revision fencing,
semantic committer and outbox developed in #13–#15 remain the foundation.
Do not add vendor confidence fields to trusted records or replace deterministic
validation with a classifier. Evolving model input to accept a Core-owned
Context Pack is already #16's responsibility, independent of TypeSafe.

## #16: first delivery and selector interface

Implement one internal semantic-selection seam inside Context Assembly, with
a deterministic reference adapter and an optional first-party Jev adapter.
This is not a new universal provider/plugin framework. The generative
ModelProvider remains a separate responsibility.

### Inputs and outputs

The selector receives only:

- the current normalized Communication and policy-permitted context;
- a bounded candidate catalogue prepared by the Core, containing stable record
  IDs, record kinds, compact summaries and captured semantic revisions;
- explicit call/input budgets and the versioned question/rubric definition.

It returns ranked candidate IDs, per-candidate assessments where available,
an outcome (`completed`, `abstained`, `unavailable`) and provider metadata.
Metadata includes provider/model ID, question/rubric version, usage and latency.
Retain the primitive-specific raw probability/distribution/score in Evaluation
Trace; do not pretend all primitive scores are calibrated probabilities.
Selection reasons are Core-defined structural/ranking/fallback reasons, not
generated explanations from Jev.

The Core validates response shape, finite/in-range values according to the
primitive, duplicate/unknown IDs and membership in the supplied catalogue.
It owns the final selected set, budget enforcement, read revisions and
disclosure eligibility. A selector cannot return new records, widen scope,
change policy or perform unrestricted retrieval.

### Workflow

```text
Communication
  → deterministic structural scope and mandatory records
  → bounded candidate catalogue
  → deterministic selector OR configured Jev selector
  → Core merges mandatory records and ranked candidates within budget
  → Context Pack with reasons, revisions and disclosure eligibility
  → ModelProvider produces a Semantic Proposal
  → deterministic validation, Grounding and atomic commit
```

Use per-candidate `Score` or `Noul` questions for relevance: one Communication
may involve multiple Contexts. Do not force the whole turn into a single
winning `Choice`. For later single-reference resolution, `Choice` includes
`none`, `new` and `ambiguous` as appropriate. Missing candidates cannot be
invented by Jev. Independent questions cannot consume each other's answers;
dependent choices must be composed explicitly and charged to the same budget.

Mandatory records, including structurally necessary pending Grounding, cannot
be discarded by semantic ranking. If mandatory records exceed the budget,
return an explicit budget-exhausted result for bounded reconciliation; do not
silently omit them or exceed the budget.

### Configuration, failure and fallback

- Select the adapter statically at bootstrap; the deterministic adapter is the
  default and requires no TypeSafe account or credentials. Provider selection
  is deployment configuration, never sender input or a runtime control plane.
- The Jev adapter uses a pinned model ID and versioned questions. `jev-latest`
  is not a reproducible deployment setting. The researched `jev-1.13.0` is a
  candidate version, not a permanently mandated model; reverify provider
  limits and SDK/API compatibility when implementing #16.
- Credentials use existing deployment secret references. Explicitly selecting
  Jev with missing configuration fails bootstrap; the default deterministic
  configuration never requires those secrets.
- Jev is a cloud text/JSON adapter. Send only the permitted bounded input;
  respect both Core and provider request/token limits. No on-premise inference
  or raw Artifact support is assumed.
- Provider calls run outside semantic transactions. Bound calls, latency,
  retries and fallback work. Timeouts, transient failures and unusable outputs
  fall back to the deterministic selection within the remaining budget; they
  do not manufacture a semantic rejection or trusted mutation.
- Ambiguous/abstained choices never become forced identity resolutions. Use
  conservative deterministic selection or later disambiguation as appropriate.
  Exhausted budgets produce an explicit operational/budget outcome.
- Thresholds are primitive-, task-, version- and domain-specific deployment
  values justified by evaluation. No universal `0.9` threshold and no confidence
  threshold authorizes Grounding, disclosure or a commit.

### Verification and adoption

At the Core service seam, use deterministic adapters and captured Jev outputs
to verify bounded multi-Context selection, mandatory-item preservation,
unknown/duplicate ID rejection, ambiguous choices, timeout/malformed-output
fallback, budget exhaustion, disclosure restrictions and replay. No live
provider access or API key is required in CI.

Run a small, optional live comparison before enabling Jev for a deployment,
using the existing domain scenario fixtures and independent expected results.
Compare recall of necessary records, erroneous selections, fallback rate,
end-to-end latency and total cost (including retries/fallbacks) with the
reference selection. Include Italian, ambiguous references, multiple subjects,
older pending Grounding and synonymous concepts. Record the model/rubric
versions, operating point and adoption decision. Retain the reference adapter
when quality or operational benefit is not demonstrated. Do not introduce a
benchmark runner or a broad model-comparison framework.

## Later bounded uses

| Ticket | Planned use | Required limit |
| --- | --- | --- |
| #16/#17 | Resolve references among selected Entity/Context IDs and reuse Canonical Claim Concepts or persisted Emergent Concepts. | Unknown/new/ambiguous results require normal resolution or creation flow. No Actor identity inference, ontology promotion, automatic concept merges or rewriting history. |
| #17 | Experiment with per-item `accepted/rejected/corrected/pending` proposals. | Classifier output never constitutes acceptance evidence. Check observable exposure, source Actor, subsequent targeted evidence and Contract policy. Implicit acceptance still needs a supported rationale; Jev does not generate one. |
| #27 | Suggest permitted Artifact roles/types or select values from extracted text and pre-parsed candidates. | Keep perception/OCR/multimodal generation outside Jev. Preserve Artifact/version/source evidence; derived observations remain Candidate Claims unless an explicit Contract trusted-source rule applies. |
| #29 | Record and replay decision-model attempts alongside generative attempts. | Capture provider/model and question versions, permitted input references, raw decisions, fallback/retry, usage, latency and Context Pack revisions separately from trusted history. Replay makes no external inference call. |
| #31 | Audit coverage for bounded decision adapters and their failure modes. | Include selection errors, confident-but-wrong outputs, mixed-item responses and Italian cases. Assess expected semantic transitions and disclosure, not schema conformance or LLM consensus alone. |

Do not add these later uses as hidden extra scope to #16. They reuse the
bounded-decision approach where the owning ticket needs it; provider adoption
there requires task-specific evidence. Simple explicit confirmation forms,
numeric/calendar calculations and deterministic invariants stay in ordinary
code and do not acquire model dependencies.

An optional semantic verifier of a generative extraction may request a new
proposal or clarification. Add that extra call only when scenario failures
demonstrate a need and a measured benefit; it is not a required second judge
for every turn and never replaces Grounding or Contract validation.

## Implementation reading order

1. Read the owning GitHub ticket and SPEC #12, then this development direction.
2. Read the research note's primary sources for the adapter's actual limits.
3. Implement the ticket's tracer bullet at existing Core seams, preserving
   deterministic operation and testing without a live model.
4. Capture decisions and assess deployment adoption against the stated metrics.

The GitHub tickets are the canonical work briefs. This document records the
cross-ticket design; the research note is evidence, not production validation.
