# Conversational GTM Agent

`routes.router` exposes `/api/agent/conversations`; authenticated adapters can
call the equivalent functions from `service.py`. The surrounding tenancy
middleware must establish a real server session. Web conversations are private.
An IM adapter may explicitly create a store-visible `audience="group"`
conversation after verifying its channel binding and membership.

## Execution

1. `enqueue_turn` atomically appends the user message, creates an idempotent turn,
   and inserts a control-plane dispatch row. Reusing `client_turn_id` with the
   same input returns that turn; different input conflicts.
2. `start_agent_workers` polls durable dispatch rows. Claims have bounded leases
   and fencing tokens. Workers reconstruct membership from the stored opaque
   session hash; model output never supplies identity or permissions.
3. `runtime.run_turn` asks the model for one validated decision at a time. The
   whitelist in `models.Decision` and `tools.TOOLS` defines available actions.
   Steps are selected from actual observations rather than a fixed pipeline.
4. Generation delegates to the existing application APIs and their quota gates.
   `checkpoint_generated_artifact` must run inside the artifact-save transaction.
   A committed checkpoint survives worker interruption and prevents replaying
   expensive generation. `blocked` output is never a deliverable.
5. Completion atomically saves the turn, appends the assistant message, changes
   the current artifact reference, and marks the dispatch row terminal.

`stop_agent_workers` cancels active work and requeues claimed turns. The SQLite
test backend creates metadata automatically; PostgreSQL requires migration 05.
Dispatch stores only locators/session hashes; conversation content stays in the
existing scoped resource store.

## Memory and artifacts

Observations, preferences, corrections, and hypotheses retain source IDs,
original user quotes, creation/expiry times, and replacement links. Direct
corrections require a quote actually present in the user message or uploaded
audio transcript. Hypotheses require tool-observed source IDs. New same-subject
memory supersedes the old record; current corrections enter subsequent planning
and generation automatically. These records are editorial context, not product
facts or asserted customer outcomes.

Private histories and memories remain personal even for managers. Group tools
read only explicitly shared store materials. Product claims use actual catalog
retrieval. Artifact references are authorized again when returned and carry the
latest persisted version, including edits made in the Web editor. Text revision
uses `creative_revision.revise_creation` so the actual current copy and image
snapshots remain the source of truth.

## Extending and testing

Add a tool name to both the decision literal and tool registry, implement its
server-side authorization, and test the actual effect. Planner, provider,
generator, poster creator and reviser can be injected independently. Run:

```shell
python -m pytest tests/test_gtm_agent_core.py -q
```

Tests use real tenant memberships and sessions with isolated databases; they do
not use the development legacy identity fallback.
