# Application State API (v1)

The application API is a separate loopback HTTP transport for a deployment's consumer application. It is not the sender acquisition interface. It returns only the Current Trusted View and immutable trusted events; Candidate Claims, Semantic Proposals, normalized Communications, Evaluation Traces, outbox-delivery attempts, and secret values are never serialized.

## Deployment configuration

Enable the transport with static bootstrap configuration. Credential values are supplied only through the existing deployment `secret_references` environment mapping:

```json
{
  "secret_references": {
    "application_reader": "PT_APPLICATION_READER_TOKEN",
    "event_consumer": "PT_EVENT_CONSUMER_TOKEN"
  },
  "application_api": {
    "host": "127.0.0.1",
    "port": 8080,
    "credentials": [
      {"secret_reference": "application_reader", "scopes": ["state:read"]},
      {"secret_reference": "event_consumer", "scopes": ["events:consume"]}
    ]
  }
}
```

The only supported scopes are `state:read` and `events:consume`. A credential can list both. The service resolves each opaque bearer token at startup and does not expose it in configuration, responses, traces, or a runtime issuer. Missing configuration, an unresolved secret, an invalid bearer token, and an absent bearer token deny access. Bindings are restricted to IPv4 loopback (`127.0.0.1`); a deployment may place its own TLS/reverse-proxy boundary in front of the local service.

Start the configured transport instead of the JSONL acquisition worker:

```bash
python3 -m piecetogether --config deployment.json --serve-api
```

Rotation is deployment configuration: change the secret reference/value and restart the API service. There is no runtime token issuer, provider framework, tenancy model, or administration API.

## Endpoints

Every endpoint uses `Authorization: Bearer <opaque-token>`.

### `GET /v1/state`

Requires `state:read`. Supply exactly one of `entity_id`, `context_id`, or `actor_id` and optionally `limit` (1–100, default 50) and `cursor`.

- An Entity query returns that trusted Entity and its assertion sets.
- A Context query returns the Context and its member Entity targets.
- An Actor query returns trusted Entity/Context targets whose provenance belongs to that Actor. Authoritative assertions on selected targets remain visible with their distinct provenance.

Results include `api_version`, Current Trusted View `revision`, the query, selected Entities/Contexts, and complete assertion sets. Each assertion set retains heads, lineage/currentness, relationships, conflicts, Contract/commit metadata, and provenance. Pagination is over a bounded list of trusted Entity/Context targets; an assertion set is never split across pages. State cursors bind the query and semantic revision, so a cursor is rejected after trusted state changes rather than silently mixing snapshots.

### `GET /v1/events`

Requires `events:consume`. It accepts the same `limit` and `cursor` pagination parameters and returns immutable Grounded Change Event payloads only. Its cursor fixes an event-list watermark, so a multi-page read is stable while later events are obtained by a subsequent new read. Consumers deduplicate at-least-once events by event ID and semantic commit ID, then query the State API for the complete view.
