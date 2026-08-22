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

PieceTogether is currently in product-definition and specification. The product thesis and MVP boundaries are captured in [docs/PRD.md](docs/PRD.md); implementation has not started yet.
