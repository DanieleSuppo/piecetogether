# PieceTogether — Product Requirements Document

**Status:** Draft v0.1  
**Product:** PieceTogether  
**Repository:** `piecetogether`  
**Audience:** Product, AI engineering, backend/platform engineering  
**Scope:** Core service MVP

---

## 1. Product summary

PieceTogether is a developer-facing, stateful AI service that turns ongoing, unstructured human communication and shared artifacts into organized, traceable, evolving application state.

People communicate naturally: across messages, sessions and channels; with text, images and documents; by correcting themselves, referring to things discussed earlier, introducing new subjects, and assuming shared context.

Applications usually require a different representation: known entities, explicit relationships, current state, historical changes, persistent artifacts, provenance and predictable integration boundaries.

PieceTogether bridges those two worlds without forcing the sender to communicate through forms or predefined completion workflows.

The central product thesis is:

> **People communicate naturally. Applications need structured state. PieceTogether continuously pieces the two together.**

PieceTogether is not primarily an extraction engine. Its differentiator is the construction of **shared, grounded understanding over time**.

---

## 2. Problem

Existing application workflows frequently require people to translate what they know into the structure expected by software.

This appears in many professional relationships:

- a customer communicating preferences and decisions to a stylist;
- a client sharing requirements, images, measurements and documents with an interior designer;
- a person supplying information and documents for an insurance or administrative process;
- a patient describing facts and observations to a professional;
- any workflow in which one person needs to provide rich, evolving information to another person or system.

The sender may communicate:

- five relevant facts or five hundred;
- in one session or across months;
- on one channel or several;
- using text, images and documents;
- about one object or several;
- by introducing new entities that were not known when the interaction began;
- by correcting, refining or contradicting previous statements;
- by referring implicitly to prior contexts or artifacts.

A form, static schema-completion workflow or single-shot `LLM -> JSON` extraction does not adequately model this interaction.

The receiving application needs a trustworthy representation of what has been communicated, how it has changed and why the system believes it.

---

## 3. Product goals

### G1 — Natural acquisition

Allow people to provide useful information in natural, unstructured communication without requiring them to understand or complete the application's data model.

### G2 — Multimodal understanding

Treat text, images and documents as first-class inputs. Media may be temporary evidence, persistent source evidence, a domain artifact, or more than one of these simultaneously.

### G3 — Continuous state

Maintain knowledge across messages, sessions and channels. Session boundaries must not define memory boundaries.

### G4 — Conversational grounding

Before information derived from human communication becomes trusted state, make the interpretation observable to the sender inside a natural conversation and give the sender a real opportunity to confirm, continue, refine or correct it.

### G5 — History preservation

Preserve the semantic history of grounded information. Corrections and supersessions must not silently rewrite how previous understanding was reached.

### G6 — Domain control

Allow the consuming application to define which entity types, relationships, artifact types and relevant policies are permitted. The AI may discover new instances inside that controlled domain; it may not arbitrarily redefine the domain.

### G7 — Traceability

Maintain provenance linking trusted conversational state to normalized communications, artifacts and grounding exchanges.

### G8 — Safe application integration

Expose trusted current state and trusted change events through stable application-facing interfaces. LLM output alone must never be equivalent to committed state.

### G9 — Standalone operation

Run as an autonomous service without requiring an existing business backend. External systems are optional providers and destinations, not prerequisites.

### G10 — Evaluability

Make semantic behavior measurable through expected state transitions, not merely through subjective assessment of conversational quality.

---

## 4. Non-goals

The MVP is not intended to be:

- a conversational form builder;
- a generic `LLM -> JSON` wrapper;
- a CRM or case-management product;
- a personal memory assistant;
- a general-purpose knowledge-base chat interface;
- a RAG retrieval assistant for end users;
- a workflow engine;
- an autonomous business-action agent;
- a multi-tenant SaaS control plane;
- a marketplace for third-party plugins;
- an identity-management or automatic account-linking product;
- an ontology-learning system that autonomously changes the authoritative Domain Contract;
- a requirement-completion engine that continuously asks for unspecified or "missing" fields.

Retrieval/admin capabilities may later be built on top of PieceTogether, but they require a different authorization and disclosure model and are explicitly outside the acquisition-interface MVP.

---

## 5. Primary users and actors

### 5.1 Application developer

Integrates PieceTogether into a product or professional workflow.

The developer:

- provides the Domain Contract;
- configures channel plugins and providers;
- configures identity mappings or an identity provider;
- consumes State API and grounded events;
- defines policies governing persistence, grounding requirements and allowed external effects.

### 5.2 Sender / conversational user

The person naturally supplying information.

The sender should not need to know PieceTogether's entity model, claim model or internal state machine.

### 5.3 Consumer application

The vertical application using PieceTogether, for example:

- the public Interior Design reference application;
- a private fashion/stylist application;
- a future professional workflow.

The consumer application is not embedded inside PieceTogether and does not directly access PieceTogether's persistence layer.

### 5.4 Domain owner

The person or team responsible for deciding which domain concepts and policies are authoritative for a deployment.

This actor may be the same as the application developer.

---

## 6. Core product invariants

These invariants are product requirements, not optional implementation details.

### I1 — No silent trusted extraction

An LLM-extracted conversational claim cannot directly become trusted state.

### I2 — Observable interpretation

Before conversational information becomes grounded, the interpretation must have been made meaningfully observable to the sender.

### I3 — Subsequent confirmation signal

A grounded claim requires a later sender contribution providing explicit or conversationally implicit evidence of acceptance. Silence or elapsed time alone is insufficient.

### I4 — Domain-constrained creation

The AI may create new instances of entity types allowed by the Domain Contract. It may not invent arbitrary new entity types outside that contract.

### I5 — History-preserving knowledge

Grounded historical claims are not destructively overwritten by later corrections.

### I6 — Provenance retained

Trusted state must preserve a traceable relationship to its source and grounding history, subject to configured retention/deletion policy.

### I7 — LLMs propose; Core commits

LLMs produce typed semantic proposals. Deterministic Core logic validates proposals before atomic trusted-state mutations.

### I8 — Acquisition is not general retrieval

The sender-facing conversation may use historical state internally but may disclose stored information only when reasonably necessary for contextual continuity, disambiguation or grounding.

### I9 — External actions require application policy

PieceTogether may produce trusted state and grounded events. An LLM may not autonomously decide to perform unrelated external business effects.

### I10 — Memory belongs to PieceTogether

Provider-managed LLM threads or sessions are never the authoritative source of durable state.

### I11 — Channel continuity, not autonomous channel switching

State is shared across configured channels for the same resolved actor. PieceTogether does not proactively move a grounding exchange to another channel without explicit future policy.

### I12 — Trusted conversational knowledge is distinct from authoritative reference data

Grounded communication-derived information and explicitly trusted external reference information retain different provenance and may coexist or conflict.

---

## 7. Conceptual model

### 7.1 Communication

A normalized inbound or outbound conversational contribution independent of transport-specific payloads.

A Communication should retain sufficient information for:

- actor identity;
- channel provenance;
- ordering/time metadata;
- reply/reference relationships;
- text/content;
- artifact references;
- grounding history.

Raw Telegram updates, MIME payloads and provider-specific webhook structures are transport details and are not part of the durable semantic model.

### 7.2 Entity

A domain object of a type permitted by the Domain Contract.

Examples in different domains might include:

- `Person`;
- `Room`;
- `Occasion`;
- `Garment`;
- `Quote`;
- `Document`.

PieceTogether may identify existing entities or propose new instances of allowed types.

### 7.3 Context

A first-class, lightweight semantic scope representing what participants are talking about together.

A Context is separate from:

- a chat session;
- an email thread;
- a domain entity.

Examples:

- kitchen renovation;
- outfit for a gala;
- discussion of a particular episode or subject.

A Context may reference multiple entities and may be suspended, resumed and revisited across sessions or channels.

A single Communication may contribute to multiple Contexts.

### 7.4 Candidate Claim

A model interpretation that has not yet completed grounding.

Candidate Claims belong to working state and may be changed, rejected or discarded.

They are not trusted knowledge.

### 7.5 Grounded Claim

A conversational claim whose interpretation:

1. was made observable to the sender; and
2. was subsequently accepted explicitly or conversationally implicitly according to policy.

Grounding establishes shared understanding of what was communicated. It does not prove that the communicated proposition is objectively true in the external world.

### 7.6 Authoritative Reference Assertion

Information provided by a source explicitly configured as authoritative for a deployment.

It is not called "grounded" unless it separately passes through conversational grounding.

Grounded and authoritative assertions are distinct provenance classes, not a simple confidence ladder.

### 7.7 Claim relation

A history-preserving semantic relationship such as:

- confirms;
- refines;
- corrects;
- supersedes;
- contradicts.

The exact initial relation vocabulary will be specified during design, but correction/supersession and conflict preservation are required in the MVP.

### 7.8 Artifact

A media/document object associated with communication and/or domain state.

An Artifact may act as:

- ephemeral evidence;
- retained source evidence;
- persistent domain artifact.

The Core owns artifact metadata, provenance, lifecycle and semantic relationships. Binary content is stored through an `ArtifactStore`.

### 7.9 Grounding

A structured record connecting:

- one or more candidate claims or entity/context resolutions;
- the assistant communication that exposed the interpretation;
- the sender contribution that accepted, rejected, corrected or left it unresolved;
- the applicable grounding policy.

Grounding is part of the semantic model, not merely text left inside a chat transcript.

---

## 8. Domain Contract

Each deployment operates under a supplied Domain Contract.

The Domain Contract controls the world PieceTogether is allowed to represent, including as appropriate:

- allowed entity types;
- allowed relationships;
- artifact types;
- artifact persistence requirements;
- known/canonical claim concepts;
- grounding policies;
- validation rules;
- other domain-level guardrails.

The contract does **not** imply a completion checklist.

An entity may have one useful claim or hundreds. PieceTogether must not continuously ask for fields merely because they are absent from a predefined model.

### Open decision: canonical vs emergent knowledge

The final model for canonical, open and hybrid claims remains intentionally unresolved.

The following requirement is already fixed:

> PieceTogether must preserve non-canonical concepts it has previously created or suggested and make relevant prior concepts available during future context resolution, so that semantically similar information can preferentially reuse or reconcile existing concepts rather than repeatedly inventing synonyms.

Automatic promotion of emergent concepts into the authoritative Domain Contract is outside the MVP.

---

## 9. Conversational grounding protocol

### 9.1 Natural acknowledgement

Grounding must be expressible as normal conversation rather than mandatory confirmation forms.

Example:

```text
Sender:
I want to keep the parquet.

PieceTogether:
Got it — we’ll keep the existing parquet.
For the cabinets, are you still leaning toward light wood?

Sender:
Yes, but probably a warmer tone.
```

The sender did not answer a formal yes/no question about the parquet, but the later contribution accepted the conversational frame and continued.

### 9.2 Explicit grounding

Domain policy may require explicit confirmation for particular semantic operations or classes of information.

Examples may include:

- a sensitive authorization;
- replacement of a significant current artifact;
- a high-consequence interpretation.

### 9.3 Partial correction

One assistant response may expose multiple candidate interpretations.

A subsequent sender contribution may:

- accept some;
- reject some;
- correct some;
- leave some unresolved.

The Grounding model must preserve this granularity.

### 9.4 Persistent pending grounding

An unresolved grounding must survive:

- the end of a session;
- channel changes initiated by the sender;
- later unrelated communication.

Later communication may resolve an older pending grounding.

### 9.5 Contextual resurfacing

PieceTogether may surface an older pending grounding when it becomes relevant to understanding or grounding the current communication.

The MVP must not proactively chase open groundings merely because they remain unresolved.

---

## 10. Multichannel behavior

The MVP includes first-party channel support for:

- Email;
- Telegram.

Channel implementations live in the PieceTogether repository as trusted first-party modules.

There is:

- no plugin marketplace;
- no dynamic user code loading;
- no untrusted plugin execution requirement.

### 10.1 Channel plugin contract

A Channel Plugin normalizes channel-specific input into the canonical Communication model and delivers PieceTogether conversational responses back through that channel.

Plugins declare capabilities such as:

- receive;
- reply;
- attachments;
- threading;
- voice.

The Core must reason about declared capabilities rather than provider-specific APIs.

### 10.2 Email semantics

There is one semantic Email Channel Plugin.

Transport/provider differences such as a particular inbound email service, mail API or SMTP/IMAP implementation are configuration/transport concerns rather than separate semantic channel plugins.

### 10.3 Cross-channel identity

Channel plugins expose their technical identity.

Identity linking is provided through deployment configuration or a configured identity provider.

The LLM must not infer that two channel identities belong to the same actor.

### 10.4 Same-channel grounding

The MVP grounds a communication on its originating channel.

PieceTogether does not automatically initiate contact through another channel to complete grounding.

If the sender independently communicates through another linked channel and that communication resolves an earlier pending grounding, the Core may apply it.

---

## 11. Artifact lifecycle

PieceTogether must distinguish semantic artifact role from storage mechanism.

### 11.1 Artifact metadata ownership

The Core owns:

- artifact identity;
- type;
- provenance;
- related entities/contexts;
- lifecycle;
- version/supersession relationships;
- persistence policy;
- storage reference.

### 11.2 Artifact bytes

Binary content is managed through an `ArtifactStore` extension point.

An MVP deployment may use a simple local persistent implementation. Other stores may be added without changing the Grounding Engine.

### 11.3 Persistent artifact ownership

When policy determines that an artifact is persistent, PieceTogether should take control of its storage through the configured ArtifactStore instead of relying indefinitely on a channel/provider URL.

### 11.4 Derived information

Observations extracted from artifacts become Candidate Claims and follow the same grounding requirements as other communication-derived information unless the Domain Contract explicitly provides a different trusted-source rule.

---

## 12. Memory and context assembly

PieceTogether owns durable memory and must not depend on opaque provider session state.

Each processing turn builds an explicit bounded context.

### 12.1 Context assembly stages

The expected conceptual pipeline is:

1. deterministic structural scope;
2. compact broad memory catalogue;
3. high-recall semantic routing;
4. retrieval of selected content;
5. primary grounding/interpretation model;
6. bounded iterative retrieval if required.

### 12.2 Deterministic scope

Deterministic/structured signals should be used before semantic retrieval where available, including:

- actor;
- explicit reply/reference metadata;
- directly identified entities;
- active/recent Contexts;
- relevant pending Groundings;
- Domain Contract and policies;
- structural relationships.

### 12.3 Semantic router

The MVP should evaluate using a cheaper/faster model as a high-recall context router before the main grounding model.

This is an optimization strategy to be validated through evaluation, not a requirement to use two particular model vendors.

The router should optimize primarily for recall: an irrelevant retrieved item generally costs tokens; a missed necessary item may cause incorrect understanding.

### 12.4 Bounded iterative retrieval

The main model may state that the supplied context is insufficient and request additional semantic context.

The Core controls retrieval scope and resource budgets.

The model must not receive unrestricted free-form access to the entire persistent memory.

---

## 13. Semantic Proposal boundary

LLMs do not mutate trusted state.

Model outputs affecting state must use a typed, versioned Semantic Proposal representation.

Semantic Proposals may express concepts such as:

- candidate entity resolution;
- candidate new allowed entity instance;
- candidate Context creation/resumption;
- candidate claims;
- candidate claim relationships;
- grounding plans;
- grounding resolution;
- artifact classification/lifecycle proposal;
- trusted-state mutation proposal after successful grounding.

The exact schema belongs to technical specification, but the public boundary between probabilistic interpretation and deterministic validation is a product invariant.

### 13.1 Deterministic validation

Before commit, the Core validates at minimum:

- entity and relation types against the Domain Contract;
- referenced objects exist where required;
- grounding requirements were satisfied;
- the proposed change is compatible with the relevant semantic state;
- artifact policies are respected;
- concurrency revision remains valid;
- no forbidden domain type was invented.

Invalid proposals never become trusted state.

---

## 14. Knowledge history and current state

PieceTogether maintains separate concepts for:

### 14.1 Working state

Candidate interpretations and pending reasoning.

Mutable and discardable.

### 14.2 Grounded semantic history

History-preserving record of conversationally grounded knowledge and its relationships.

### 14.3 Reference state

Configured external information, with explicit provenance/trust classification.

### 14.4 Current Trusted View

A useful current-state projection derived from relevant grounded and authoritative assertions according to policy.

Conflicting information must not be silently erased to manufacture a single apparent truth.

---

## 15. Persistence and retention

### 15.1 Normalized communication history

Normalized communications are durable Core state because they are required for:

- provenance;
- grounding;
- pending grounding resolution;
- semantic debugging;
- context reconstruction.

Retention is configurable.

### 15.2 Raw transport payloads

Provider-specific raw payloads are not required as permanent semantic state.

They may be retained temporarily for diagnostics according to deployment policy.

### 15.3 Grounded history

Semantic history is history-preserving but remains subject to explicit retention, privacy and deletion policies.

"History-preserving" is a semantic guarantee against silent overwrite, not a promise that data can never legally or operationally be deleted.

### 15.4 Observability traces

Model attempts, discarded candidates and operational traces may be retained separately for evaluation/debugging.

They must not be confused with trusted knowledge history.

---

## 16. Concurrency and ordering

PieceTogether must support communication arriving through multiple channels while previous messages are still being processed.

### 16.1 Inbound persistence

Normalized inbound communication should be durably recorded before long-running semantic processing.

### 16.2 Concurrent interpretation

Independent interpretation work may occur concurrently.

### 16.3 Serialized semantic effect through atomic commit

Trusted semantic mutations must use atomic transactions and optimistic concurrency.

If semantically relevant state changed while a proposal was being prepared, PieceTogether must reconcile or reprocess before commit.

### 16.4 Event ordering

Grounded change events are emitted only after successful semantic commit.

Channel timestamps, receipt times, processing times and grounding times should not be collapsed into a fictitious perfect global ordering.

---

## 17. Integration surface

Consumer applications interact with PieceTogether through stable application-facing interfaces, never by directly reading Core persistence.

### 17.1 State API

Provides the current trusted state of permitted entities and Contexts to authorized application/developer consumers.

This is distinct from the restricted sender-facing acquisition interface.

### 17.2 Grounded change events

Expose trusted semantic changes after successful commit.

Examples conceptually include:

- claim grounded;
- claim corrected/superseded;
- entity created/resolved;
- artifact persisted/superseded;
- Context created/resumed.

The exact event schema belongs to technical specification.

### 17.3 Projection sinks

Projection integrations may consume grounded events to communicate trusted changes to downstream systems.

Initial MVP support should remain minimal; a simple HTTP/webhook projection is sufficient to establish the boundary.

Database, spreadsheet and other connectors should be added only for real consumers.

---

## 18. Extension architecture

PieceTogether uses typed extension points rather than one universal plugin abstraction.

Expected extension families include:

- `ChannelPlugin`;
- `DomainContractProvider`;
- `ReferenceStateProvider`;
- `ProjectionSink`;
- `ArtifactStore`;
- `ModelProvider`.

First-party channel plugins live in the PieceTogether repository and are statically included in the deployment.

The MVP does not require:

- dynamic plugin loading;
- sandboxing;
- remote plugin protocols;
- multiple implementation languages;
- independent plugin services.

In-process first-party modules are preferred unless a concrete future requirement justifies additional operational isolation.

---

## 19. Deployment model

PieceTogether is a standalone stateful service.

For the MVP:

> **One PieceTogether Core deployment serves one application deployment.**

Example:

```text
Interior Design Demo
        │
        │ stable API
        ▼
Dedicated PieceTogether instance
```

A private vertical application may deploy a separate instance of the same released PieceTogether service.

### 19.1 Configuration

Core capabilities and providers are configured at deployment.

The consumer application does not need to import PieceTogether source code and does not use the normal data API as a runtime control plane.

A future shared/multi-tenant PieceTogether deployment would require a genuine control plane, tenant isolation, per-tenant secrets, routing and authorization. That is outside the MVP.

### 19.2 Standalone mode

A minimal deployment must be possible using local/configured providers without any pre-existing business backend.

---

## 20. Security and disclosure

### 20.1 Sender-facing minimum disclosure

The acquisition conversation may use historical information internally but may surface previously stored information only to the extent reasonably necessary for:

- contextual continuity;
- disambiguation;
- grounding.

General retrieval requests through the sender-facing acquisition channel are out of scope and must not automatically expose stored state.

### 20.2 Internal read vs external disclosure

The product must enforce the distinction:

> **Can read internally != can disclose conversationally.**

### 20.3 Future retrieval/admin capability

A future professional/admin/retrieval interface requires its own:

- authentication;
- authorization;
- audit;
- data-scope rules;
- disclosure policies.

It must not be treated as a trivial flag on the acquisition interface.

### 20.4 Secrets

Channel and provider credentials belong to deployment secret management, not checked-in domain/demo configuration.

---

## 21. Reference application

A separate public repository will contain an Interior Design / Renovation reference application.

It is not part of the PieceTogether repository.

The reference application is responsible for:

- an Interior Design Domain Contract;
- demo identity mappings;
- demo/reference UI;
- canonical evaluation scenarios/fixtures;
- deployment composition/configuration.

It consumes a released PieceTogether service and the first-party Email/Telegram capabilities provided by that service.

Its purpose is to stress the general Core with a concrete, understandable domain rather than to turn PieceTogether into an interior-design product.

A domain-specific concept leaking into the Core is considered an architectural smell.

---

## 22. Canonical reference scenarios

The MVP must be able to represent and evaluate at least the following scenario families.

### S1 — Simple new information

A sender communicates a new preference/fact about an existing entity. The Core interprets it, exposes the interpretation naturally and commits it only after grounding.

### S2 — New entity/context emergence

Communication introduces a new instance of an allowed entity type and an associated Context without requiring the sender to explicitly create database objects.

### S3 — Return to an older Context

A sender switches subject and later returns to an older Context. The new information is associated with the correct scope.

### S4 — Correction

A sender corrects previously grounded information. Historical state is preserved and the current state changes only after grounding.

### S5 — Ambiguous reference

The sender uses an ambiguous phrase such as "the lighter one" when several plausible prior artifacts exist. The Core must not silently choose when disambiguation is required.

### S6 — Artifact role

One attachment is temporary visual evidence while another must become a persistent domain artifact.

### S7 — Artifact supersession

A new persistent document supersedes an older document without deleting historical provenance.

### S8 — Cross-session/cross-channel continuity

Semantically related communication arrives through Telegram, then Email, then Telegram and contributes to the same resolved actor/state.

### S9 — Pending grounding resolution

A previously exposed but unresolved interpretation is explicitly or implicitly resolved by later communication, potentially on another sender-chosen channel.

### S10 — No retrieval leakage

A sender asks the acquisition interface to retrieve historical stored information. The Core does not behave as a general-purpose retrieval agent.

### S11 — Necessary historical disclosure

Historical state may be minimally referenced when needed to disambiguate or ground current inbound information.

### S12 — Domain guardrail

The model attempts or appears semantically tempted to create an entity type not allowed by the active Domain Contract. The mutation is rejected.

### S13 — Concurrent correction

Multiple communications arrive while related interpretation is in progress. The trusted commit reflects current semantic state and does not commit stale contradictory proposals.

### S14 — Authoritative vs grounded conflict

Trusted external reference state conflicts with a newly grounded human assertion. Both provenances are preserved; the conflict is not silently resolved.

---

## 23. MVP functional requirements

### Communication and channels

- FR-001 The service SHALL accept normalized textual communication.
- FR-002 The MVP SHALL support image input.
- FR-003 The MVP SHALL support document/PDF input.
- FR-004 The MVP SHALL include Email and Telegram first-party channel plugins.
- FR-005 The service SHALL maintain state independently from channel sessions.
- FR-006 Cross-channel continuity SHALL require an explicitly resolved common actor identity.

### Domain and entities

- FR-010 Each deployment SHALL operate under a Domain Contract.
- FR-011 The service SHALL reject creation of entity types not permitted by the Domain Contract.
- FR-012 The service SHALL support creation of new instances of allowed entity types.
- FR-013 The service SHALL support relationships between domain entities.
- FR-014 The service SHALL maintain first-class Contexts independent of channel sessions.

### Grounding

- FR-020 Conversationally derived claims SHALL begin as non-trusted candidates.
- FR-021 Candidate interpretations SHALL be made observable to the sender before they can become grounded.
- FR-022 Grounding SHALL require a later sender contribution indicating sufficient acceptance.
- FR-023 Silence/time passage alone SHALL NOT resolve grounding.
- FR-024 The service SHALL support explicit and conversationally implicit grounding.
- FR-025 The service SHALL support partial acceptance/correction of multi-claim grounding.
- FR-026 Domain policy SHALL be able to require explicit grounding for selected classes of information/operation.
- FR-027 Pending groundings SHALL persist across sessions.
- FR-028 Later sender communication MAY resolve older pending grounding.
- FR-029 The MVP SHALL NOT proactively chase unresolved grounding on another channel.

### History and provenance

- FR-030 Grounded semantic claims SHALL preserve correction/supersession history.
- FR-031 Current trusted state SHALL be derived separately from historical claims.
- FR-032 Trusted conversational state SHALL retain provenance to normalized communication and grounding history.
- FR-033 Candidate/model-attempt history SHALL NOT be treated as trusted knowledge.

### Artifacts

- FR-040 Artifact metadata/lifecycle SHALL be owned by the Core.
- FR-041 Artifact bytes SHALL be stored through an ArtifactStore abstraction.
- FR-042 The service SHALL distinguish temporary evidence from persistent artifacts according to policy.
- FR-043 Persistent artifact supersession SHALL preserve historical relationships.

### Context and memory

- FR-050 Durable state SHALL be owned by PieceTogether rather than by an LLM provider session.
- FR-051 The service SHALL assemble bounded context for each semantic processing turn.
- FR-052 Context assembly SHALL use structural/domain information before relying solely on semantic similarity.
- FR-053 The service SHALL support bounded iterative retrieval.
- FR-054 The system SHALL preserve and make relevant previous emergent concepts available for later semantic reuse/reconciliation.

### Semantic proposals and state safety

- FR-060 LLMs SHALL NOT directly write trusted ledger state.
- FR-061 State-affecting model output SHALL be represented as typed/versioned Semantic Proposals.
- FR-062 The Core SHALL validate Semantic Proposals against Domain Contract and current state.
- FR-063 Trusted mutations SHALL commit atomically.
- FR-064 The Core SHALL use concurrency/version checks before trusted commit.
- FR-065 Grounded events SHALL be emitted only after successful commit.

### External integration

- FR-070 The Core SHALL expose a State API for authorized application consumers.
- FR-071 The Core SHALL expose grounded semantic change events.
- FR-072 Candidate interpretations SHALL NOT be exposed as trusted application state.
- FR-073 External business effects SHALL require explicit application/projection policy.

### Security/disclosure

- FR-080 The sender-facing acquisition interface SHALL NOT operate as unrestricted historical retrieval.
- FR-081 Historical information MAY be surfaced only when justified by contextual continuity, disambiguation or grounding.
- FR-082 Authoritative reference data and grounded conversational data SHALL preserve distinct provenance.

### Deployment

- FR-090 PieceTogether SHALL run as a standalone stateful service.
- FR-091 The MVP deployment model SHALL be single-application per Core instance.
- FR-092 Core configuration SHALL be supplied as deployment configuration rather than through a multi-tenant runtime control plane.
- FR-093 The Core SHALL be usable without a pre-existing business backend.

---

## 24. Evaluation requirements

PieceTogether must be evaluated against expected semantic state transitions.

The evaluation suite should include metrics for:

- claim interpretation accuracy;
- entity resolution accuracy;
- Context resolution accuracy;
- grounding correctness;
- implicit-confirmation accuracy;
- premature grounding rate;
- correction/supersession accuracy;
- artifact-role classification;
- provenance completeness;
- context retrieval recall;
- context retrieval precision;
- missed clarification rate;
- unnecessary clarification rate;
- cross-channel state consistency;
- disclosure-policy violations;
- Domain Contract violations;
- stale/concurrent semantic commit errors;
- latency;
- model/token usage;
- cost.

### 24.1 Baselines

At minimum, evaluation should compare PieceTogether behavior against relevant simpler approaches such as:

1. single-shot multimodal extraction to structured output;
2. stateful extraction without conversational grounding;
3. main-model-only context assembly versus cheap-router + main model, where applicable.

The purpose is to demonstrate which complexity contributes measurable product value.

### 24.2 Expected-state fixtures

Canonical scenarios should specify:

- inbound communications;
- available historical state;
- expected entity/context resolution;
- expected candidate/grounding behavior;
- expected final trusted state;
- expected historical relationships;
- allowed/forbidden disclosure.

A plausible natural-language response alone is not a passing condition.

---

## 25. MVP success criteria

The MVP is successful if a reference consumer can demonstrate, reproducibly, that PieceTogether:

1. receives ongoing text/image/document communication from Email and Telegram;
2. maintains continuity for the same resolved actor across those channels;
3. distinguishes independent and resumed semantic Contexts;
4. creates new allowed domain instances without inventing forbidden entity types;
5. turns extracted information into trusted state only through the defined grounding protocol;
6. preserves pending grounding across time;
7. correctly handles correction, supersession and artifact version history;
8. retains usable provenance;
9. prevents sender-facing general-purpose retrieval leakage in canonical safety tests;
10. exposes trusted current state and committed semantic changes to an application;
11. remains functional without an external business backend;
12. provides an evaluation suite showing semantic-state correctness beyond conversational plausibility.

Numeric quality thresholds will be established after the canonical scenario set and baseline runs exist.

---

## 26. Open product decisions

The following are intentionally unresolved and must not be silently decided during implementation.

### O1 — Canonical vs emergent claim model

How open, closed or hybrid claim vocabularies should be per entity/domain type.

Fixed requirement: prior emergent concepts must be reusable/reconcilable and cannot simply disappear between conversations.

### O2 — Context router strategy

Whether the cheap-model high-recall router provides enough accuracy/cost benefit over structural/vector retrieval plus the main model to remain in the default MVP architecture.

This should be settled through evaluation.

### O3 — Initial Domain Contract representation

The exact contract syntax/schema is a technical/product-design decision still to be specified.

### O4 — Initial persistence technologies

Database, vector/semantic index and artifact-store implementation choices are intentionally not part of this PRD.

### O5 — Concrete email transport

The MVP requires the semantic Email Channel Plugin, not a specific email provider.

### O6 — Authentication for application-facing APIs

The security boundary is required, but concrete authentication technology belongs to technical design.

---

## 27. Deferred capabilities

Explicitly deferred until justified by real consumers:

- multi-tenant/shared Core control plane;
- general sender-facing retrieval;
- admin/professional knowledge exploration UI;
- automatic cross-channel identity linking;
- proactive follow-up/attention engine for unresolved groundings;
- automatic channel switching for grounding;
- autonomous ontology evolution;
- automatic promotion of emergent concepts to canonical concepts;
- plugin marketplace or remote/untrusted plugins;
- broad database/CRM/spreadsheet connector catalogue;
- general workflow automation;
- autonomous business-action planning.

---

## 28. Repository and consumer boundaries

This repository contains the PieceTogether Core product, including first-party trusted plugins.

It does not contain the Interior Design reference application.

Expected public separation:

```text
piecetogether
    Core service
    first-party plugins
    Core API/contracts
    tests/evaluation infrastructure

piecetogether-demo-interior-design
    Interior Design Domain Contract
    reference UI
    identity/demo configuration
    canonical scenarios
    deployment composition
```

Private vertical applications may consume released PieceTogether versions through the same service boundary.

Core and consumer lifecycles remain independent.

---

## 29. Product principle summary

PieceTogether is built around a small set of product principles:

> **Let people communicate naturally.**

> **Do not confuse extraction with understanding.**

> **Make understanding observable before trusting it.**

> **Preserve how knowledge changed, not only its latest value.**

> **Let the application control the domain while allowing information to emerge naturally inside it.**

> **Use AI for semantic judgment and deterministic code for invariants.**

> **Keep memory inside PieceTogether, not inside an opaque model session.**

> **Acquire safely; treat retrieval as a separate security surface.**

> **Build reusable infrastructure only when real consumers prove it is reusable.**

---

## 30. Next step

This PRD defines the product and MVP boundary.

The next phase should produce a technical specification that resolves implementation-level questions without reopening the product invariants defined here, beginning with:

- Semantic Proposal model;
- Grounding state machine;
- Domain Contract format;
- persistent conceptual data model;
- Context Assembly interfaces;
- extension/plugin contracts;
- State API and grounded event contracts;
- canonical evaluation fixtures.
