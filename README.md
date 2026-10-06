# PieceTogether

**Turn ongoing, unstructured human communication into organized, trustworthy application state.**

People do not naturally communicate in schemas.

They send messages, reply days later, switch channels, share images and documents, correct themselves, refer to things discussed weeks ago, introduce new subjects, and assume the other person remembers the context.

Professional software usually expects the opposite: structured records, known entities, explicit relationships, current values, traceable documents, and predictable state.

**PieceTogether** is a developer-facing, stateful AI service that bridges those two worlds.

It receives ongoing multimodal, multichannel communication, understands what it refers to, organizes what emerges into a domain controlled by the application, conversationally verifies that understanding, and maintains an evolving history that downstream software can safely use.

---

## The problem

A customer should not need to think like the CRM used by a professional.

A patient should not need to know the data model of a clinical application.

A client working with an interior designer should be able to say:

> “For the kitchen, keep the parquet. I’m sending you the updated electrician quote too — this replaces the previous one.”

and continue two days later by email:

> “I measured the wall again. I was wrong: it’s 3.42 m, not 3.20.”

The application behind the professional may need to understand that:

- the message refers to the existing **Kitchen** context;
- keeping the parquet is a newly communicated preference;
- the attached file is a persistent **Quote** artifact;
- the new quote supersedes an older artifact;
- `3.42 m` corrects a previously grounded measurement;
- the old measurement must remain in the historical record;
- the user must have an opportunity to correct the system’s interpretation before it becomes trusted state.

A normal extraction pipeline can produce JSON from a message.

That is not enough.

The difficult part is maintaining **shared understanding over time**.

---

## What the service does

The service is built around five related capabilities.

### Understand

Interpret unstructured communication across text, images and documents.

Identify which known entities or contexts a message refers to, when a new allowed entity is emerging, what information is being communicated, and which artifacts are relevant.

### Ground

Make the system’s interpretation visible inside the natural conversation.

Instead of silently extracting and storing information, the service reflects what it understood in a conversational way so the sender can confirm, continue, refine or correct it.

A claim becomes trusted conversational knowledge only after that grounding succeeds.

### Remember

Maintain persistent state across messages, sessions and channels.

The service preserves:

- entities;
- contexts;
- grounded claims;
- relationships;
- corrections and supersessions;
- artifacts;
- provenance;
- grounding history;
- normalized communication history;
- unresolved groundings.

The current state is a projection of this history, not a destructive overwrite of it.

### Reconcile

Connect new communication with what already exists.

A new message may:

- update an existing entity;
- return to an older context;
- introduce a new instance of an allowed entity type;
- refine an earlier claim;
- correct or supersede previous information;
- conflict with authoritative external state;
- resolve a grounding left open in a previous session or channel.

### Expose

Applications consume trusted state through a State API and grounded change events.

The AI constructs understanding.  
Application policy decides what external effects are allowed.

---

## A conversation is not a form

The service does **not** assume that a target schema must be completed.

A person may provide five pieces of useful information or five hundred. Both are valid.

The Domain Contract describes the world the application understands and constrains what the AI may create. It does not automatically define a checklist that the user must complete.

For example, an interior-design application may allow:

```text
Client
Property
RenovationProject
Room
Quote
Document
```

The AI may discover a new `Room` or `Quote` instance from the conversation.

It may not invent an unrelated entity type such as `Disease` just because the model believes it would be useful.

**The application controls the domain.  
Instances and knowledge may emerge from the conversation.**

Exactly how canonical and emergent concepts coexist is intentionally still an open product-design question. The Core must, however, preserve and make previously used non-canonical concepts available to future context resolution rather than repeatedly inventing synonymous concepts from scratch.

---

## Conversational grounding

Grounding is not a final “Confirm: Yes / No” screen.

It follows normal closed-loop human communication.

For example:

```text
User:
I like this one, but I’d make it slightly longer
and soften the shoulders.

Service:
Got it — we’ll keep this as the reference,
but with a slightly longer line and softer shoulders.
Do you still like the colour as it is?

User:
Yes, especially for evening events.
```

The response naturally exposes the interpretation. The sender can accept it by continuing, explicitly confirm it, or correct only the part that is wrong.

Internally, the service knows which candidate claims were exposed by that response.

```text
candidate C17 → longer line
candidate C18 → softer shoulders

grounding G9
  acknowledges: C17, C18
```

If the user replies:

```text
Longer, yes. The shoulders are fine as they are.
```

the service can resolve:

```text
C17 → grounded
C18 → rejected
new corrected candidate → produced
```

### Strong grounding invariant

A conversational claim cannot enter trusted state merely because the model extracted it.

It must first be made observable to the sender, and a later contribution from that sender must provide sufficient explicit or conversationally implicit evidence of acceptance.

Silence alone is not confirmation.

Domain policy may require explicit confirmation for sensitive classes of information.

---

## Contexts are first-class

A chat session is not the unit of memory.

The same conversation may contain several subjects, and the same subject may reappear across multiple sessions and channels.

The Core therefore models **Context** separately from domain entities.

```text
Actor
├── Context: Kitchen renovation
│   ├── Room: Kitchen
│   ├── claims
│   └── artifacts
│
└── Context: Bathroom renovation
    ├── Room: Bathroom
    ├── claims
    └── artifacts
```

A context is a lightweight semantic scope: what the participants are talking about together.

Contexts may be created, suspended, resumed and related without being tied to Telegram chats, email threads or model-provider sessions.

A single communication may contribute to multiple contexts.

---

## Multichannel continuity

The Core owns durable state. Channels do not.

For example:

```text
Monday — Telegram
“For the kitchen, keep the parquet.”

Wednesday — Email
“I’m attaching the revised electrician quote.
This replaces the one I sent last week.”

Friday — Telegram
“I measured that kitchen wall again.
It’s actually 3.42 m, not 3.20.”
```

All three communications may contribute to the same evolving state.

The initial first-party channel plugins are:

- **Email**
- **Telegram**

Channel plugins are trusted, first-party modules in the Core repository. They implement a stable channel contract and declare capabilities such as:

```text
receive
reply
attachments
threading
voice
```

They are included explicitly in a deployment; there is no plugin marketplace, dynamic user installation or untrusted plugin execution.

An Email plugin is a single semantic channel plugin. The concrete email transport/provider is configuration, not a different Core plugin.

Cross-channel continuity does not imply automatic cross-channel outreach. A grounding started on one channel is not proactively moved to another unless a future application policy explicitly allows it.

---

## Identity across channels

Identity discovery is not an AI task.

Each channel integration resolves its technical identity and the deployment maps channel identities to a normalized actor.

For example:

```text
telegram: 918273
email: alice@example.com
        ↓
actor: client-42
```

Automatic identity linking, OTP flows and identity-management products are outside the Core MVP.

---

## Multimodal artifacts

Images and documents are not all treated the same way.

An attachment may be:

### Ephemeral evidence

Used to understand the communication and then removed according to policy.

### Source evidence

Retained as provenance for information derived from it.

### Domain artifact

A meaningful object in the professional workflow itself, such as:

- a floor plan;
- an updated quote;
- an insurance document;
- a signed authorization;
- a visual reference that the domain requires to remain available.

An artifact can have more than one role.

The Domain Contract and application policy govern what kinds of artifacts are allowed and whether they must be retained.

The Core always owns artifact metadata, provenance, lifecycle and semantic relationships.

Binary content is stored through an `ArtifactStore` extension point rather than inside the semantic ledger.

---

## History, not destructive updates

Trusted conversational knowledge is history-preserving.

If a user first says:

```text
Kitchen wall length = 3.20 m
```

and later corrects it:

```text
Kitchen wall length = 3.42 m
```

the first claim is not rewritten.

```text
C1
wall length = 3.20 m
grounded
superseded

C2
wall length = 3.42 m
grounded
supersedes C1
current
```

The application-facing current state contains `3.42 m`, while the ledger preserves how that state came to exist.

This is semantic history preservation, not a requirement to event-source every operational detail of the service.

Candidate interpretations and model attempts belong to working state and observability, not to the trusted knowledge history.

---

## Grounded and authoritative information are different

Not all trusted information needs to come from a conversation.

A configured Reference State Provider may supply application-owned data such as existing customers, projects or records.

The Core preserves the distinction between:

```text
GROUNDED
information whose meaning was established
through conversational shared understanding

AUTHORITATIVE
information supplied by a source explicitly
trusted by the application

CANDIDATE
an interpretation that is not trusted state
```

Grounded and authoritative information may support each other or conflict.

Conflicts are preserved rather than silently resolved by the model.

Application policy determines which provenance classes are sufficient for specific downstream uses.

---

## Acquisition, not end-user retrieval

The end-user conversational interface is designed to **acquire and ground information**, not to expose everything the Core knows.

The Core must read historical state internally to maintain continuity, resolve references and detect contradictions.

That does not mean the end user can use the acquisition channel as a general-purpose knowledge query interface.

```text
CAN READ INTERNALLY
≠
CAN DISCLOSE EXTERNALLY
```

Previously stored information may be surfaced only when reasonably necessary for:

- contextual continuity;
- disambiguation;
- conversational grounding.

General-purpose retrieval, admin exploration and professional-facing knowledge access require a separate authorization model and are outside the Core MVP.

---

## Memory and context assembly

The Core owns all durable conversational and semantic state.

LLM-provider threads or session memory are never authoritative.

Each processing turn assembles an explicit, bounded context from persistent state.

A typical pipeline is:

```text
New communication
        ↓
Deterministic structural scope
        ↓
Broad compact memory catalogue
        ↓
Cheap/high-recall semantic context router
        ↓
Fetch relevant content
        ↓
Main grounding model
        ↓
Need more context?
   ├── no
   └── yes → bounded iterative retrieval
```

The initial router is an optimization strategy, not a requirement to use a particular model.

The key principle is:

> **Rules narrow. Retrieval proposes. The model resolves.**

The main model may request additional context, but retrieval is bounded and controlled by the Core.

This keeps internal memory access powerful without exposing unrestricted retrieval to the conversational user.

---

## The AI never writes trusted state directly

LLMs produce typed, versioned **Semantic Proposals**.

They do not mutate the ledger.

```text
Communication
      ↓
LLM interpretation
      ↓
Semantic Proposal
      ↓
Core validation
      ↓
Atomic semantic commit
      ↓
Trusted state
      ↓
Grounded events
```

The Core deterministically validates proposals against:

- the Domain Contract;
- allowed entity types and relationships;
- grounding history;
- artifact policy;
- current ledger state;
- semantic revision/concurrency constraints.

Only a valid commit can produce trusted state or external grounded events.

---

## Concurrency

Messages from multiple channels may arrive while previous communications are still being processed.

The Core does not assume perfectly sequential conversations.

Inbound communication is persisted immediately and processing may occur concurrently.

Trusted semantic mutations use atomic commit and optimistic concurrency.

If semantically relevant state changed while a model was processing a message, the Core must reconcile or reprocess before committing the result.

Grounded events are emitted only after a successful semantic commit.

---

## Application boundary

The Core is a **standalone, stateful service**, not a library embedded into every vertical application.

For the MVP:

> **one Core deployment = one application deployment**

A consumer application talks to the Core through a stable service API.

Configuration is owned by deployment, not by application code and not by a multi-tenant runtime control plane.

```text
Interior Design Demo
        │
        │ API
        ▼
Dedicated Core instance
```

Another application uses another instance of the same released Core.

A future shared/multi-tenant deployment would require a separate control plane and is intentionally outside the MVP.

---

## External boundaries and extensions

The Core uses typed extension points rather than one universal plugin mechanism.

Expected extension families include:

```text
ChannelPlugin
DomainContractProvider
ReferenceStateProvider
ProjectionSink
ArtifactStore
ModelProvider
```

The MVP implements only the extensions required to demonstrate the product.

Infrastructure should emerge from real consumers rather than being built speculatively.

---

## How applications consume trusted state

The public integration boundary has two complementary forms.

### State API

Ask for the current trusted state of entities and contexts.

### Grounded change events

React when trusted conversational state changes.

```text
Grounding Core
     │
     ├── State API
     │     “What is true in the current trusted view?”
     │
     └── Grounded Events
           “What trusted change just happened?”
```

Candidate interpretations never cross this integration boundary as if they were trusted information.

External side effects remain controlled by explicit application or projection policy.

The AI may understand that a new document supersedes an old one. It does not autonomously decide to delete records or trigger unrelated business workflows.

---

## Example reference application

The Core is intentionally domain-independent.

A separate public repository will provide an **Interior Design / Renovation reference application**.

It is not an interior-design feature inside this repository.

The reference application supplies its own:

- Domain Contract;
- demo identity mappings;
- user interface;
- scenarios and fixtures;
- deployment configuration.

It consumes a released Core service and the first-party Email and Telegram channel plugins.

A private production application such as a stylist/customer workflow can consume the same Core without becoming part of the public reference implementation.

This separation is intentional:

```text
PUBLIC CORE
      │
      ├── Public Interior Design Demo
      ├── Future public consumers
      │
      └── Private consumers
```

A domain-specific concept leaking into the Core is considered an architectural smell.

---

## What this is not

This project is **not**:

- a conversational form builder;
- an “LLM to JSON” wrapper;
- a CRM;
- a general-purpose knowledge base;
- a personal-memory assistant;
- a RAG chat interface over stored data;
- a workflow engine;
- an autonomous business-action agent;
- a universal connector marketplace;
- a requirement to complete a predefined schema.

Its job is narrower:

> **Transform ongoing, natural human communication and shared artifacts into organized, traceable, evolving state that applications can safely build on.**

---

## MVP thesis

The first Core release must demonstrate that it can reliably:

1. receive text, images and documents;
2. operate across Email and Telegram;
3. maintain state across sessions and channels;
4. resolve existing and newly emerging allowed entities;
5. maintain first-class semantic contexts;
6. extract candidate claims without treating them as trusted;
7. conversationally expose its interpretation;
8. resolve explicit and implicit grounding;
9. preserve unresolved groundings across time;
10. ground an older pending item when later communication resolves it;
11. preserve correction and supersession history;
12. distinguish ephemeral evidence from persistent artifacts;
13. maintain provenance from trusted state back to normalized communication and grounding;
14. consume authoritative reference state without confusing it with conversational grounding;
15. prevent general-purpose information leakage through the acquisition interface;
16. validate typed Semantic Proposals before trusted state changes;
17. expose current trusted state and grounded events;
18. handle concurrent communication without unsafe semantic commits;
19. assemble bounded historical context and perform bounded iterative retrieval when necessary;
20. run as a standalone single-application service without requiring an external business backend.

---

## Evaluation

The system must be evaluated on its **semantic state transitions**, not only on whether the model produces plausible conversation.

Candidate evaluation dimensions include:

- claim interpretation accuracy;
- entity/context resolution accuracy;
- grounding correctness;
- correction/supersession accuracy;
- artifact-role classification accuracy;
- unnecessary clarification rate;
- missed clarification rate;
- information disclosure violations;
- provenance completeness;
- context retrieval recall and precision;
- cross-channel state consistency;
- cost and latency.

The reference scenario set should define an expected ledger state, not merely an expected natural-language answer.

---

## Design principles

### Natural communication first

Do not make the sender adapt to the application’s data model.

### Shared understanding before trusted state

Information derived from human communication becomes decision-grade only through grounding.

### Domain freedom within application guardrails

The model may discover instances and relationships inside the world the application allows. It may not silently redefine that world.

### History before overwrite

Corrections add knowledge about change; they do not erase how previous understanding was reached.

### Provenance by construction

Trusted state should retain a traceable relationship to why the Core believes it.

### Memory belongs to the Core

Provider-managed LLM sessions are disposable optimizations, never authoritative state.

### AI for semantics, deterministic code for invariants

Models interpret. The Core validates and commits.

### Acquisition and retrieval are separate security surfaces

Internal access to memory does not imply conversational permission to disclose it.

### Vertical before infrastructure

Extension points exist to support real applications. Connector frameworks and platform capabilities are added only when concrete consumers require them.

---

## Status

The development Core implements durable text ingress and observable interpretation ([#13](https://github.com/DanieleSuppo/piecetogether/issues/13)), deterministic Domain Contract and typed Semantic Proposal validation ([#14](https://github.com/DanieleSuppo/piecetogether/issues/14)), atomic trusted semantic history ([#15](https://github.com/DanieleSuppo/piecetogether/issues/15)), and bounded Context Packs with disclosure controls ([#16](https://github.com/DanieleSuppo/piecetogether/issues/16)). The complete conversational Grounding lifecycle, Current Trusted View, production channels and application APIs remain subsequent tickets. Product boundaries are captured in [docs/PRD.md](docs/PRD.md); technical authority is [SPEC #12](https://github.com/DanieleSuppo/piecetogether/issues/12).

## Run the development Core

Requires Python 3.10+; no runtime dependencies or external services.

```bash
python3 -m piecetogether --config config/development.json
```

This standalone local development process accepts one normalized JSON Communication per stdin line and emits one response per stdout line:

```json
{"channel":"development","sender":"sender-1","idempotency_key":"message-1","text":"Keep the original option.","sent_at":"2026-01-01T12:00:00Z"}
```

Replies contain only `communication_id`, `status` and `reply`. Repeated delivery returns the same completed or rejected outcome, including after restarting the process. Reusing a key with different content is rejected. `retryable` denotes an operational failure; `reprocess_required` denotes a stale Contract, semantic revision, Context Pack or exposure binding. Both have no exposed reply; redeliver the original Communication for a fresh attempt. `budget_exhausted` is an operational limit with no reply or semantic rejection; inspect its trace and reconcile the mandatory backlog or deployment budget before retrying. `rejected` denotes a deterministic, terminal policy or invariant failure and has no reply.

`config/development.json` selects the static development ChannelPlugin, deterministic ModelProvider, declarative Contract file and version, enabled capabilities and identity mappings. Relative database and Contract paths resolve from the config file's directory; absolute paths are also supported. Optional `secret_references` map names to environment variable names; values stay outside configuration. The development adapters need no credentials. Unknown adapters and unmapped senders are rejected; runtime input cannot change configuration.

SQLite stores normalized inbound/outbound Communications, candidate proposals, processing outcomes and per-attempt Evaluation Traces separately from trusted semantic history and non-authoritative vocabulary. Validation checkpoints atomically persist the proposal, outbound, outcome and attempt evidence; accepted semantic commits also append trusted history, Grounding outcomes, revision and an outbox event in that transaction. The development ChannelPlugin accepts handoff into a durable local mailbox; it does not send Email or Telegram. Model and channel work run outside database transactions. State API, Current Trusted View and event dispatch are subsequent tickets.

Run one sequential development worker per database. This transport is operator-local, not a public network ingress or production connector. Candidates and traces are not returned to senders. A local operator with deployment filesystem access can inspect an outcome separately:

```bash
python3 -m piecetogether --config config/development.json --inspect <communication_id>
```

Inspection returns the latest turn fields and an ordered `attempts` collection containing each attempt's number, status, proposal, outbound and trace. Reprocessing preserves earlier stale decisions and captured proposals. Startup imports the last available checkpoint from older deployments once; earlier attempts that those deployments already overwrote cannot be reconstructed.

## Declarative Domain Contracts and Proposals

The `contract` bootstrap field names a JSON file in the format of [config/development-contract.json](config/development-contract.json). Its `version` must match `contract_version`; `schema_version` is integer `1`. The Core loads and validates a Contract snapshot at startup. Domain rules are data, never executable plugins. Unknown fields, duplicate JSON keys, unsupported schema versions, contradictory constraints and dangling policy/type references fail bootstrap.

The development Contract deliberately declares no domain Entity Types. The deterministic model produces only a free-text candidate interpretation, not structured domain assertions. Omitting `contract` uses the same closed, empty domain with the configured version, preserving existing development configurations. Deployments declare their own vocabulary through these catalogues:

| Catalogue | Declaration |
| --- | --- |
| `entity_types` | Type name → `creation` boolean and `attributes` map of value constraints. No required conversational attributes. |
| `relationship_types` | Relationship name → nonempty `source_types` and `target_types` arrays. Entity Type names and `$context` are allowed; `$claim` also supports claim relationships such as `corrects`, `supersedes` and `contradicts`. |
| `claim_concepts` | Canonical concept → `target_types`, `value` constraint and declared `grounding_policy` name. Targets are Entity Types or `$context`. |
| `grounding_policies` | Policy name → nonempty `acceptance` array containing `explicit`, `implicit`, or both. Optional nonempty `confirmation_forms` string array declares exact explicit confirmation forms, compared after trimming and case-folding; it requires `explicit` acceptance. The foundational default explicit form is `yes`. Silence is not an acceptance mode. |
| `artifact_types` | Artifact Type → permitted `roles`, `persistence` (`forbidden`, `allowed`, `required`), `retention` and `supersession` boolean. Roles are `ephemeral-evidence`, `source-evidence`, `persistent-domain-artifact`. Retention independently declares `metadata`, `bytes`, `provenance` as `retain` or `delete`. Required persistence needs the persistent role and retained bytes. |
| `emergent_concepts` | `{"allowed": false}`, or `allowed: true` with `target_types`, `value` and `grounding_policy`. These constraints govern every non-canonical concept; proposals cannot override them. |

Value constraints support `type` (`string`, `boolean`, `integer`, `number`, `object`, `array`), `enum`, numeric `minimum`/`maximum`, string/array `min_length`/`max_length`, object `properties`, and array `items`. Unknown object properties are forbidden; absent properties are allowed. Arrays require an item constraint. Numbers must be finite; booleans are distinct from integers. Constraint nesting is limited to 16. Names/references are bounded to 256 characters. The `$context`, `$claim` and `$artifact` type names are reserved by the Core.

Model adapters return `SemanticProposal` records from `piecetogether.proposals`. Envelope schema `1` versions both the envelope and its operation union. Operations are frozen typed records with durable `kind` discriminators:

- `EntityOperation`, `ContextOperation`: propose candidate creation or resolution.
- `ClaimOperation`: propose a constrained candidate assertion; provenance defaults to the envelope's inbound Communication and may never refer to another sender's input.
- `RelationshipOperation`: propose a Contract-permitted relationship with permitted endpoints.
- `GroundingPlanOperation`, `GroundingResolutionOperation`: describe a policy-governed plan or resolution. A plan's `claim_ids` expose Claims; optional `resolution_ids` expose Entity/Context resolutions as independent items. A resolution names a Core-generated item ID and later sender evidence; optional `rationale` supports the later conversational-policy extension.
- `ArtifactOperation`: describe role classification or lifecycle intent, with Contract-controlled retention.
- `EmergentConceptOperation`: introduce reusable vocabulary with `non_authoritative` status; no canonical-name collisions, Entity Type creation or ontology promotion.
- `ContextRequestOperation`: request known catalogue IDs for `continuity`, `disambiguation` or `grounding`, with a positive bounded record budget. Core resolves requests before another model call; the final proposal must contain no unresolved request.
- `DisclosureOperation`: request Core-rendered historical content by selected record ID and eligible purpose. Unknown, duplicate or ineligible references reject without a reply.

Proposal-local identifiers and targets within a Grounding plan must be unique. References resolve independently of operation order, and every operation must validate before any reply is handed off. An envelope contains at most 256 operations and 256 free-text candidates. Free-text `candidate_claims` remain unstructured Working State and cannot stand in for a structured Claim operation or trusted assertion. Concept names and type names are separate namespaces; a Concept name can never be used to introduce an Entity Type. The active Contract version, Proposal version/intent, validation outcome (`accepted`, `rejected`, `stale`) and deterministic reason codes are recorded through operator inspection. Unserializable rejected model output is omitted with an explicit trace marker; the rejection itself remains durable.

Delivery retries restore typed operations from the captured JSON and validate against the active Contract before handoff. Valid retries reuse the same outbound identifier and draft without another model invocation. A changed Contract reference produces `reprocess_required` and clears the current reply; redelivery then obtains a fresh proposal. Legacy envelopes receive the same version and policy checks, including rejection of boolean schema versions. A completed historical outcome remains idempotent. Storage failures remain operational retries, and an interrupted legacy rejection with already-recorded validation evidence is recovered without replacing its decision.

Candidate acceptance authorizes Working State, not trusted mutation. For planned items the Core appends an observable rendering of every proposed interpretation and its relationships to the draft, binding the values to what the sender actually sees; model prose alone cannot expose hidden assertions. The complete response remains bounded to 65,536 characters. A Grounding plan becomes exposed/pending only after durable outbound recording and accepted channel handoff, recorded atomically with completion. Failed or indeterminate delivery leaves no groundable items; an idempotent delivery retry produces one set of stable Core-generated item IDs. A legacy cached draft missing this binding requires fresh processing. Artifact classification remains candidate-only; persistent content and lifecycle require the later Artifact tickets.

## Atomic trusted semantic history

A `semantic_commit` Proposal must carry a nonnegative integer `semantic_revision` and only `GroundingResolutionOperation` operations for accepted, Core-owned pending items. The foundational writer checks the active Contract, original exposed proposal, item policy, resolved Actor, later receipt after accepted exposure, `reply_to` addressing that outbound Communication, and the Contract's explicit confirmation form. Model assertions and arbitrary implicit rationales are not acceptance evidence. Rich implicit acceptance, rejection/correction outcomes, successor-candidate orchestration and later Context Pack selection belong to #17/#16.

Each Entity or Context creation needs its own accepted resolution item. Accepting a Claim cannot implicitly authorize its unaccepted target or Context members. Partial accepted batches may commit dependencies first and remaining items later. Resolve operations use existing trusted IDs and cannot replace Entity attributes or Context membership; acceptance preserves their identity.

The short SQLite transaction rechecks the semantic revision and appends immutable Entities, Contexts, Grounded Claims, Grounding snapshots/items, provenance links, relationships, an opaque semantic commit ID and Contract version. It advances the revision and inserts a trusted change in the outbox together with the processing checkpoint. A storage failure rolls all these writes back. A rejected or stale proposal leaves trusted history unchanged. The initial deployment-wide revision conservatively fences all trusted changes; #22 can refine the relevant scope without relaxing atomicity.

Corrections, supersessions and contradictions must be Contract-permitted relationships in the exposed candidate graph. They connect a newly accepted Claim to assertions on the same target/concept; correction and supersession predecessors must already be committed. A differing assertion on the same semantic key needs an explicit history relationship to conflicting current heads. Previous assertions are never overwritten. Current Trusted View projection and its conflict representation belong to #18.

A committed turn's delivery failure cannot undo trusted history. Redelivery restores the durable commit and outbound without another model call or semantic effect, even after a Contract change or restart. Completed historical outcomes remain idempotent. A losing processing attempt, including a model/parsing failure, cannot replace an already committed checkpoint; its trace is retained separately. The outbox is recorded atomically now; at-least-once event dispatch and consumer authentication belong to #23/#24.

Accepted Emergent Concept declarations persist in a separate `non_authoritative` catalogue with source Communication, Actor and Contract provenance. Later declarations preserve earlier source descriptions. Previously declared names are available to validation and later Core-owned reconciliation without redeclaration, but every use still satisfies the active emergent policy or canonical constraints. Vocabulary never creates Entity Types or modifies the Contract.

Operator-only in-process inspection seams are:

```python
core.inspect(communication_id)  # proposal/trace plus exposed Grounding Items
core.inspect_history()         # revision, immutable objects, Groundings, relations, commits, outbox
core.inspect_concepts()        # separate non-authoritative vocabulary and provenance
```

These are local Core/verification interfaces, not sender retrieval or an application State API. Sender replies still contain only `communication_id`, `status` and `reply`. Deterministic adapter scenarios in `tests/test_history.py` exercise authorized commits and failure/recovery invariants without a live model.

## Bounded Context Packs and semantic decision adapters

`ModelProvider.propose(inbound, contract_version, context_pack)` receives a frozen Core-owned Pack. It includes resolved Actor identity, the active declarative Contract/policy JSON, selected record IDs/kinds, reasons, content revisions, deployment-wide semantic revision, scope fingerprint and explicit budgets. The bounded catalogue supports multiple Contexts, Entity/Claim history, normalized Communications, unresolved Grounding, persisted Emergent Concepts and validated candidate Artifact metadata. Artifact bytes and persistence are handled by the later Artifact tickets.

The deterministic reference selector ranks compact summaries by lexical overlap and explicit IDs. It does not force ambiguous references into one identity or create a record when none matches. Actor-owned pending items and explicit trusted record references are mandatory. Mandatory overflow produces `budget_exhausted`; optional catalogue overflow is recorded as truncation. The initial policy conservatively treats every Actor-owned pending item as mandatory. Catalogue bounds prioritize pending items and recent records; older optional history outside the catalogue is unavailable to a model request.

Internal selection is distinct from sender disclosure. Structural evidence marks pending items addressed by `reply_to` as eligible for Grounding, explicitly referenced trusted IDs as eligible for continuity/disambiguation, and same-thread Communication history as eligible for continuity. Ranking and additional-context requests never add eligibility. The generative model receives identities/revisions for internal-only records with their content and summaries withheld; unselected catalogue entries never include payloads. This conservative first policy prevents private historical content from leaking into model-generated prose or candidates. Eligible historic content uses `DisclosureOperation` and Core rendering. When a historical catalogue is present, arbitrary model draft prose is replaced by the current Communication's acquisition acknowledgement. `SemanticProposal.response_intent='retrieval'` produces a fixed refusal without ledger disclosure; retrieval intent cannot create candidates, Grounding plans, vocabulary or trusted state. External Entity/Context/Claim/relationship references must belong to the Actor-scoped catalogue, independently of global Contract validation.

The Pack is checked after provider work and again in the checkpoint transaction. Trusted history, other turns' exposures/concepts and completed Communication membership are fenced; a turn's own durable vocabulary/exposure changes do not invalidate its delivery recovery. Cached delivery restores the Pack and selection/retrieval trace without repeating provider work. Completed and committed outcomes retain their existing idempotency guarantees.

An optional `selection` bootstrap object controls the fixed adapter and budgets. Defaults:

```json
{"adapter":"deterministic","max_records":32,"max_bytes":65536,"catalogue_records":128,"catalogue_bytes":32768,"max_calls":3,"max_tokens":65536,"timeout_ms":2000,"max_model_calls":3,"rubric_version":"context-relevance-v1"}
```

`max_records`/`max_bytes` bound selected content; `max_bytes` also bounds the serialized generative input including Communication, Contract and catalogue. `catalogue_records`/`catalogue_bytes` bound compact selector candidates. `max_calls`, `max_tokens` and `timeout_ms` bound selection and fallback; UTF-8 byte counts provide a conservative text-token upper bound, and fallback is charged against remaining calls/input budget. `max_model_calls` bounds the cumulative proposal/retrieval loop. Request budgets count record IDs and cannot override these deployment limits. No automatic remote retry is performed.

Optional Jev configuration requires an evaluated operating point, pinned model and resolved deployment secret:

```json
{"selection":{"adapter":"jev","model_id":"jev-1.13.0","rubric_version":"context-relevance-v1","secret_reference":"typesafe","minimum_relevance":0.7},"secret_references":{"typesafe":"TYPESAFE_API_KEY"}}
```

The example `0.7` is illustrative, not an endorsed threshold. Only the model whose limits have been verified (`jev-1.13.0`) is supported initially. Jev sends bounded text/JSON via stdlib HTTPS and independent per-candidate Noul questions. Core validates IDs, duplicate assessments, primitive shape/ranges, model/rubric metadata, usage and final budgets. Timeout, unavailable/abstained/malformed output uses deterministic fallback when budget remains. Scores never authorize disclosure, Grounding or trusted state. Missing configuration fails bootstrap; default deployments need no TypeSafe account.

Operator traces capture the catalogue/mandatory revision references, Pack, decisions, primitive values, model/rubric versions, usage, elapsed latency, retrieval and fallback. Captured HTTP fixtures keep CI independent of live credentials. Follow the [focused Jev adoption procedure](docs/jev-adoption.md) before enabling it in a deployment.

TypeSafe/Jev evaluation starts with the replaceable semantic selector in Context Assembly ([#16](https://github.com/DanieleSuppo/piecetogether/issues/16)). A deterministic reference adapter remains the default; the optional Jev adapter ranks permitted candidates while the Core owns scope, budgets, revisions and disclosure. Production adoption depends on domain-specific recall, fallback, latency and cost measurements.

Later tickets can reuse the approach for known Entity/Context references, concept reconciliation, per-item Grounding proposals and Artifact text classifications. Generative ModelProviders still handle open interpretations and conversational responses; all model decisions remain proposals subject to deterministic validation and Grounding. Confidence never authorizes trusted state.

Read the [development design and ticket responsibilities](docs/superpowers/specs/2026-10-05-semantic-decision-routing-design.md) before implementing #16, #17, #27, #29 or #31. The [research note](docs/research/2026-10-05-typesafe.md) records primary sources and provider limitations. This direction requires no change to the implemented #13–#15 foundation.

## Verify

```bash
python3 -m unittest discover -s tests -v
uvx mypy --strict piecetogether
```

The tests use the Core service boundary and deterministic adapters with temporary persistent databases, not live models or storage-internal assertions. `uvx` is needed only for the optional development typechecker.
