# PieceTogether Context

## Grounding

A structured record of an observable assistant interpretation, the applicable grounding policy, and later sender evidence that resolves or leaves unresolved the interpretations it exposed. Grounding establishes shared conversational understanding; it does not establish objective external truth.

## Grounding Item

One independently resolved interpretation within a Grounding. Each Grounding Item has its own candidate target and disposition, so one sender contribution can accept, reject, correct, or leave pending different interpretations exposed in the same assistant communication.

## Grounding Outcome

The disposition of a Grounding Item: pending, accepted, rejected, or corrected. Retention or deletion is not a Grounding Outcome and never implies acceptance.

## Domain Contract

The deployment-scoped, authoritative description of the domain the Core may represent and validate. It permits entities, relationships, artifact policies, grounding policies, and canonical claim concepts without imposing a completion checklist on the sender.

## Canonical Claim Concept

A claim concept explicitly declared by the active Domain Contract. It is part of the authoritative domain vocabulary for that deployment.

## Emergent Concept

A reusable, deployment-local semantic concept recorded from prior communication but not declared by the Domain Contract. It may be retrieved and reconciled with later communication, but it neither creates a new entity type nor promotes itself into the authoritative domain vocabulary.

## Actor

A Core identity representing the person or participant behind one or more explicitly resolved channel identities. An Actor is distinct from a domain Entity and is not inferred by the model from cross-channel similarity.

## Entity

A trusted domain object of an Entity Type permitted by the active Domain Contract. It is distinct from a Context and from an Actor.

## Context

A trusted, first-class semantic scope for what participants are discussing. A Context is independent of channels, sessions, threads, Actors, and domain Entities, though it can relate to them.

## Grounded Claim

A trusted conversational assertion committed only after its Candidate Claim has completed Grounding. It retains the Communications, Grounding, Artifact, Contract, and semantic relationships that explain its provenance.

## Authoritative Reference Assertion

An assertion supplied by an explicitly trusted Reference State Provider. It remains distinct from a Grounded Claim even when the two agree or conflict.

## Current Trusted View

An evidence-preserving projection of current Grounded Claims and Authoritative Reference Assertions. It exposes conflicting current assertions and their provenance instead of manufacturing one apparent truth.
