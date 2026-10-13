# Sender-initiated pending Grounding continuity (#28)

Email and Telegram workers can participate in **one Core deployment** using the
existing commands. They share Core persistence, not transport threads or model
sessions. No new connector, identity service, scheduler, or retrieval endpoint is
required.

## Static deployment configuration

The supplied `config/email.json` and `config/telegram.json` are intentionally
isolated examples: their databases and ArtifactStores differ. Equal Actor names
in different databases do **not** provide continuity.

Create deployment-specific Email and Telegram JSON files without overwriting
existing deployments. Retain their channel-specific transport configuration,
capabilities, secret references and environment handling. Configure both files
with the same resolved paths and semantic configuration:

```json
{
  "database": "/srv/piecetogether/core.sqlite3",
  "artifact_store": "/srv/piecetogether/artifacts",
  "contract": "/srv/piecetogether/domain-contract.json",
  "contract_version": "deployment-v1",
  "model": "openai-responses",
  "openai": {
    "model_id": "gpt-4.1-2025-04-14",
    "secret_reference": "model"
  }
}
```

This is a **common fragment**, not a complete bootstrap file. Each file still
needs its existing `channel`, transport, `capabilities`, and secrets declaration;
add `"model": "OPENAI_API_KEY"` to each file's `secret_references`. The Contract
must declare the deployment's Entity Types, Claim concepts and Grounding policies.
Keep selection budgets and media permissions consistent. Attachment perception is
still opt-in through Contract-valid `media_policy`; see the README multimodal
configuration. Relative paths resolve from each JSON file's directory, so identical
relative strings in different directories need not identify the same store.

Link identities only by operator configuration, for example:

```json
{"identities": {"email:alice@example.com": "actor-alice"}}
```

in the Email file, and:

```json
{"identities": {"telegram:987654321": "actor-alice"}}
```

in the Telegram file. Each worker's mapping contains only its own channel's
technical identities. Email addresses remain exact/case-sensitive; Telegram uses
the numeric User ID, not the username. `PT_TELEGRAM_IDENTITIES`, when configured,
replaces the Telegram JSON mapping at bootstrap, so check that it preserves the
intended Actor. Unmapped senders are rejected before model processing. Similar
names, message content, provider output and claimed identities cannot link Actors.

Secrets remain in process environment or the existing local `.env` beside the
JSON file. Explicit process values win; never overwrite an existing `.env` to
add another channel. SMTP and IMAP stay independent of Telegram configuration.
Use one worker per Telegram bot and the existing acquisition/recovery constraints.

The default `deterministic` adapter only echoes an unstructured acquisition
interpretation: it does **not** extract domain claims or resolve structured
Grounding. A deployment needs an appropriate configured ModelProvider and Contract
for that behavior. The OpenAI example is the already-supported opt-in adapter,
not a claim that its semantic quality has been measured.

## Manual real-transport procedure — NOT RUN

These steps require real mailbox, bot and provider credentials. Automated tests
use captured responses instead; they do not establish live provider quality.

1. Validate the two static files against the same Contract/version, database,
   ArtifactStore and explicit Actor. Start the operational workers:

   ```bash
   python3 -m piecetogether --config config/email-linked.json --serve-email
   python3 -m piecetogether --config config/telegram-linked.json --serve-telegram
   ```

2. From the mapped Email address, send supported text (optionally a bounded
   PNG/JPEG/PDF). Check that SMTP receives the interpretation in the Email thread.
   Inspect the acquired Communication using the existing local operator commands;
   planned items must be exposed/pending, with no Grounded Claim yet.
3. Restart workers. From the linked Telegram account, send **later, item-specific
   evidence** on the bot's private chat. For a Contract with explicit form
   `Confermo`, examples are `<rendered-candidate-id> Confermo` or
   `<unique-scalar-value> Confermo`. Separate independently addressed items with
   clauses, e.g. `subject Confermo; blue Confermo`. Confirm a pending Entity target
   too, or accept it first: accepting a Claim never implicitly accepts its target.
4. Inspect Grounding outcomes and trusted provenance: source/exposure must point
   to Email, evidence to the new Telegram Communication, with the configured Actor
   and original Artifact/checksum where applicable. The reply must go only to the
   new Telegram chat/message. No additional Email is sent.
5. Repeat from Telegram to Email. Verify the new SMTP reply is addressed solely
   to the new Email sender, with that Email's thread references. A prior Telegram
   message does not redirect SMTP or create Telegram outreach.
6. Redeliver inputs and restart: expect no duplicate exposure, semantic commit,
   event or completed reply. Interrupt processing before completion and verify
   durable channel recovery; an already committed turn's reply retry must not
   invoke the model again. Indeterminate handoffs retain existing operator
   reconciliation requirements and are never blindly resent.
7. Repeat with a differently mapped Actor, unmapped sender, vague `Confermo`,
   duplicate candidate references, questions, conflicting evidence, and a request
   for all history. Expect no unauthorized resolution or historical disclosure.
   Resolve only one item and verify its siblings remain pending. Silence and
   elapsed time must produce neither acceptance nor proactive outreach.

## Safety and executable evidence

Core assembly keeps bounded Actor-owned pending items across channels. A transport
reply reference or a uniquely addressed clause can make an item eligible for
Grounding disclosure; multiple independent clauses may address different items.
A clause matching multiple pending items stays ambiguous. Eligibility permits
bounded context, **not acceptance**: the existing validator still checks exposure,
Actor, Contract/policy, subsequent receipt, exact evidence, item uniqueness,
dependencies and semantic revisions. Finite supported evidence forms are unchanged.
Unaddressed historical payloads stay withheld from the response generator; general
sender retrieval remains refused. Mandatory overflow stays `budget_exhausted`.

`python3 -m unittest discover -s tests -p test_cross_channel.py -v` exercises the
actual `--serve-email` and `--serve-telegram` command paths with temporary shared
SQLite/ArtifactStore, controlled clocks, captured IMAP/SMTP/Bot API/OpenAI peers,
and the same configured OpenAI adapter/snapshot on every dialog turn. The provider
fixture produces deterministic proposals from eligible supplied records; its small
clause grammar is **not** a claim about the live model's language understanding.
Assertions cover both directions, attachments/provenance, partial explicit and
implicit outcomes, restart/deduplication, interrupted processing, reply recovery,
Actor isolation, ambiguity, failed exposure, permitted/forbidden disclosure and
absence of outreach. Existing transport/environment/security scenarios remain in
their original suites.
