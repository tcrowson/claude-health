# App profiles

Pick one or more in config `profiles`, then copy the relevant parts into seed.md with the project's own
names. A profile says where this kind of app breaks, so the brief can point every agent at it.

## interactive (desktop app, editor, SPA, mobile app)
- **Lifecycle events:** switching the current item and its container (document, project, catalog, account);
  removal, and ids reused after it; undo / redo / paste / reset; tool or mode sessions entered and left; a load
  that fails halfway; view changes; shutdown with work in flight.
- **Write paths:** document and settings saves, autosave timers, caches, exports, imports, file moves.
- **Concurrency:** worker threads or tasks whose results land after the user moved on; timers; the UI thread.
- **Repro harness:** headless / offscreen UI, temp data folders, the app's own test fixtures.
- **Worst outcomes:** an edit lost or applied to the wrong item, a destroyed file, a crash on close.
- **Emphasis:** cartographer + lifecycle hunter; failure hunter on saves and shutdown.

## service (web API, RPC server, daemon, queue worker)
- **Lifecycle events:** request start and end; authentication and tenant switches; connection and session
  checkout and return; retries and redelivery; deploy, drain and shutdown; config reload; migrations.
- **Write paths:** database transactions, message publishes, external calls with side effects, file storage.
- **Concurrency:** request handlers sharing state, pools, caches, background jobs, idempotency of retries.
- **Repro harness:** an in-memory or containerized database, a test client, fake external services.
- **Worst outcomes:** cross-tenant data exposure, double-applied payments or writes, silent message loss.
- **Emphasis:** security hunter on (partition adds it); failure hunter on transactions and retries.

## library (SDK, package, shared module)
- **Lifecycle events:** object construction and disposal; context managers; callbacks registered and removed;
  global or module state across calls; version upgrades by callers.
- **Write paths:** anything the library writes on the caller's behalf (files, caches, network).
- **Concurrency:** thread safety of public objects; reentrancy; async and sync entry points.
- **Repro harness:** the public API from a snippet; the test suite.
- **Worst outcomes:** a breaking change in the interface, undocumented exceptions, data races in callers.
- **Emphasis:** interface (types, invariants, error modes) over internals; the structure lens on depth.

## batch (CLI, pipeline, ETL, build or data tool)
- **Lifecycle events:** a run started, interrupted and rerun; resume from a checkpoint; partial input.
- **Write paths:** output files (atomic or not), temp files, checkpoints, database loads.
- **Concurrency:** parallel workers and their shared outputs; ordering assumptions.
- **Repro harness:** a small input fixture and a temp output folder.
- **Worst outcomes:** half-written output treated as complete, a rerun that duplicates or loses data, a wrong exit code.
- **Emphasis:** failure hunter; the performance lens on input size.

## plugin (tools embedded in a host application: content-creation or CAD plugins, editor or browser extensions)
- **Lifecycle events:** host scene new / load / save; the node, object or document the tool is bound to being
  deleted or renamed; panels opened and closed; callbacks surviving a reload of the plugin; host shutdown.
- **Write paths:** scene data, node parameters, caches on disk, preferences.
- **Concurrency:** the host's main-thread rule; background work that touches host objects.
- **Host drift:** API or toolkit changes across host versions (e.g. the host moving to a new major version of its UI toolkit).
- **Repro harness:** the host's headless or batch interpreter, or a bridge to a live session.
- **Worst outcomes:** a callback on a deleted object crashing the host, scene data corrupted on save.
- **Emphasis:** cartographer on host events; failure hunter on scene writes.
