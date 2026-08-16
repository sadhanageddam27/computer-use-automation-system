# Design Write-Up

## 1. Architecture

The system is a single Python process with no services, queues, or persistence layer
beyond the filesystem. Given the brief explicitly discourages building scaling
infrastructure prematurely, I optimized for a small number of clear boundaries
over anything distributed.

Four components, each independently testable and each mapping to one core
requirement:

- **`src/agent`** - the discovery loop. An LLM (Claude) drives a live browser
  through an observe -> decide -> act loop until the goal is met or a stopping
  condition is hit. Implemented as a 2-node LangGraph graph (`reason`, `act`)
  with a conditional edge back to `reason` - the graph structure mirrors the
  loop directly, which is the actual reason to reach for a graph here rather
  than a bare `while` loop.
- **`src/artifacts`** - the typed capability schema and an exporter that turns
  a successful discovery run's log into a versioned artifact.
- **`src/replay`** - the deterministic executor. Reads a saved artifact and
  input parameters and drives the browser with **no LLM calls at all**. This
  is the path an AI agent would trigger in production.
- **`src/escalation`** and **`src/safety`** - human handoff and policy
  guardrails, used by both the discovery and replay paths.

The target surface (`target-app/`) is a Flask app I built specifically for
this assignment rather than using a public demo site: nested-table layout, no
`id`/`data-*` attributes, server-rendered, with a login/session gate. This
was a deliberate choice to actually exercise the "no clean DOM" problem the
brief describes, rather than automating a modern site that doesn't represent
the real environment. It also has built-in, deterministic error-injection
hooks (`?inject_expired=1`, `?inject_dialog=1`) so the replay engine's error
handling could be tested reproducibly instead of relying on flaky timing.

**Key trade-off:** synchronous, single-process, single-browser-context
throughout. This is the simplest thing that satisfies every core requirement,
and the brief is explicit that appropriate simplicity is rewarded over
premature scaling infrastructure. Section 4 covers what would need to change
for the real multi-tenant environment.

## 2. Artifact schema

Defined in `src/artifacts/schema.py` as Pydantic models. The design goal was
a **contract**, not a step list: something a human reviewer and a calling
agent can both understand without reading the discovery transcript.

```
Capability
  name, version, description
  target: { app, entry_url }
  inputs:  { name -> FieldSchema }   # what the caller must supply
  outputs: { name -> FieldSchema }   # what the caller gets back
  steps:   [ ArtifactStep ]
  success_checkpoint: str            # text that must appear on success
  allowlist: [ glob patterns ]
  source_discovery_log: str          # traceability back to the recording
```

Each `ArtifactStep` carries a `LocatorStrategy`:

```
role, name              # accessibility role + accessible name - PRIMARY
xpath_fallback           # captured at discovery time - fallback only
reasoning                # why this locator should be robust
risky: bool               # state-changing/irreversible - see section 6
```

**Locator strategy is the central design decision.** Role + accessible name
(matching how a screen reader would identify the element) is primary, because
it's derived from what the element *means*, not its position in the markup -
exactly the property that survives markup churn in a legacy app. An XPath
captured at discovery time is stored as a fallback only, since it's the first
thing to break when a legacy app's structure shifts. The replay engine
(`src/replay/engine.py::_resolve_locator`) tries role+name first and only
falls back to XPath if that resolution fails or is ambiguous.

The exporter (`src/artifacts/build_from_log.py`) turns a discovery log into
an artifact by **generalizing specific literal values into named input
parameters** - the values the caller passed for `member_id`, `acct_type`,
`deposit` become `{member_id}`, `{acct_type}`, `{deposit}` template
references in the recorded steps. Everything else in the flow - which
buttons get clicked, in what order - stays fixed, because it's the
capability's mechanics, not its inputs. One exception: any field whose
`input_type` was `password` is *always* parameterized, regardless of
whether it was passed as a named param, and its literal value is never
written into the artifact (see section 6).

## 3. Determinism & error handling

Replay (`src/replay/engine.py`) never calls an LLM. Determinism comes from:
fixed step order, role+name-then-XPath locator resolution with no fuzzy
matching, and explicit timeouts on every action rather than open-ended waits.

The result contract is deliberately three-way, using the brief's own
vocabulary:

- **`success`** - checkpoint text found, outputs extracted and returned.
- **`business_outcome`** - a legitimate answer, not a crash. Detected via a
  small table of text patterns (`src/replay/outcomes.py`) checked after every
  step: "no record found", "restricted for this operator", validation
  errors. Tested against real member-not-found and permission-denied cases.
- **`hard_failure`** - stops with `failed_step`, `expected`, and `observed`,
  enough to debug without re-running.

**Recoverable conditions** are handled *before* they can become hard
failures, and are logged (not silently swallowed) via `recovered_conditions`
on the result:

- *Unexpected confirmation dialog* - detected by page text, dismissed by
  proceeding automatically.
- *Session/timeout expiry* - detected by an unexpected redirect to the login
  page mid-flow, recovered by re-running the recorded login sub-sequence
  in place, then resuming at the original step.

Both were tested using the target app's own error-injection hooks
(`?inject_dialog=1`, `?inject_expired=1`) rather than mocked - the injection
happens in the target app, and the replay engine has no special-case code for
"test mode"; it reacts to the real page state.

One bug worth naming: the confirmation-dialog check originally only ran
*before* each step, but that dialog only appears as a *result* of the final
action. Testing surfaced this immediately (the replay reported a hard
failure staring at the dialog it should have handled) - fixed by running one
more recoverable-condition check after the last step completes.

## 4. Heterogeneity & multi-tenant

Not built - the brief explicitly scopes this to design only. Two questions:

**Surface abstraction (web -> legacy web -> desktop).** The seam already
exists in the code: `browser_state.py` and `tools.py` are the only places
that know about Playwright/DOM specifically. Everything above that
(`loop.py`, the artifact schema, `replay/engine.py`) operates on the
abstract concepts of "role + accessible name" and "action verbs" (click,
type, select). To extend to a desktop app, the same abstraction holds - OS
accessibility APIs (UIAutomation on Windows, AX API on macOS) expose the
same role/name concept Playwright's accessibility tree does. The
`LocatorStrategy` schema wouldn't need to change; only `browser_state.py`
and `tools.py` would need a desktop-automation implementation behind the
same interface. This is why locator strategy uses role+name rather than
anything DOM-specific - it's the part of the design that already generalizes.

**Multi-tenant reuse.** Hundreds of tenants running ~20 apps each, many on
the same underlying vendor product. I would not store one artifact per
tenant. Instead:

- Canonicalize concrete values in a step's `value` field the same way input
  parameters already are - `/member/12345` becomes `/member/:id` - so one
  artifact recorded against a "base" tenant instance is reusable across
  tenants running the same vendor product, configured/branded/versioned
  differently.
- Add a `tenant_overrides: dict[tenant_id, PartialArtifact]` layer to the
  schema for the cases where branding/config genuinely changes a locator
  (a renamed button, a reordered form) - override only the specific steps
  that differ, inheriting everything else from the base artifact.
- **Drift detection**: since `success_checkpoint` and business-outcome
  patterns are already checked on every replay, a rising failure rate on a
  specific tenant for an otherwise-stable artifact is the signal to
  re-record for that tenant rather than patch it blindly. This reuses
  machinery already built (section 3) rather than needing something new.

## 5. Escalation & handoff

Implemented in `src/escalation/handoff.py`, integrated into the replay
engine's hard-failure path via `--escalate-on-failure`.

**Control-transfer model**: automation and the human share the exact same
Playwright `page` object - there is no new browser, no new login, no fresh
context spun up for the handoff. "Pausing" means the automation process
stops acting while the page stays exactly as it was, fully interactive.
"Resuming" means automation starts acting again from whatever state the
human left the page in. In practice, with `headless=False` the browser
window is already on-screen for a human to click into directly; the
mechanism doesn't depend on a separate co-browsing console (explicitly out
of scope per the brief).

On a hard failure, if escalation is enabled: an intervention request is
written to `/evidence/` (capability name, step, reason, live URL,
screenshot) before pausing. On resume, three outcomes are checked in order:

1. Did the human complete the whole goal manually? (checkpoint text present)
2. Did their action surface a business outcome? (same detection as section 3)
3. Otherwise, retry the exact step that failed - now that the blocking
   condition has been cleared on the live session.

All three are logged as `human_intervention` recovered conditions, not just
a boolean "it worked."

**Tested, not just designed**: since a real human isn't available in this
sandbox, I used an `operator_simulator` callback that performs a genuine
corrective Playwright action on the same live page in place of a human
click, then verified automation resumed and completed correctly. This
proved the control-transfer mechanism actually works rather than only
compiling. The first version of this test caught a real bug - I initially
targeted the wrong artifact step when constructing the failure scenario, and
the test correctly failed to recover, which is exactly the kind of thing an
integration test should catch.

## 6. Safety

Three guardrails, described in the brief's own terms, all in
`src/safety/guardrails.py` and enforced in both `src/agent/loop.py` and
`src/replay/engine.py` using the same functions - a policy that behaves
differently in discovery than in replay would be a bug.

**Allowlist.** Fails closed: an empty allowlist permits nothing. Checked at
three points during replay - the artifact's declared entry URL, any
`navigate` step's target, and (defense in depth) the resulting page URL
after every action, in case a click unexpectedly navigates somewhere
unintended. Tested against a tampered artifact pointing at
`evil.example.com` - blocked before any action ran, with `failed_step:
"entry"` making the reason obvious.

**Risky/irreversible actions.** Each `ArtifactStep` carries a `risky: bool`.
The exporter defaults the last state-changing click before the success
checkpoint to risky (in this artifact, the sub-account creation submit) -
adjustable via `--risky-step`. The replay engine blocks on an explicit
confirmation before executing a risky step unless `--auto-confirm-risky` is
passed (documented as a CI/automated-testing-only flag). Tested both ways:
declining returns a `hard_failure` with a clear reason; confirming proceeds
normally.

**Redaction.** This caught a real bug during development, not a
hypothetical: an early discovery evidence log had the literal login
password stored in plaintext (`"text": "x"`) because the logging code
recorded whatever the model typed, unfiltered. Fixed by tagging each logged
element with its `input_type` from the accessibility scan and redacting any
`password`-typed value to `***REDACTED***` before it's written to disk. The
artifact exporter goes further: any step whose source was a password field
is *always* converted to a required `{password}` input parameter, even if
the caller didn't ask for that parameter explicitly - the literal value
never gets baked into the artifact at all, so it must be supplied fresh at
replay time.

## 7. Cuts

- **Multi-tenant and desktop support** - designed (section 4), not built,
  per the brief's own scope note.
- **Real-time co-browsing operator console** - explicitly out of scope in
  the brief. The handoff mechanism (section 5) is real; the operator's view
  is just the same browser window / a manual `input()` prompt, not a
  purpose-built UI.
- **Confidence scoring / approval gating for artifacts** - considered as
  the stretch goal but not built; would score an artifact by replay success
  rate over N runs and require an approval state before unattended replay.
- **Multi-run stability testing** - replaying an artifact N times and
  reporting a flakiness signal. Straightforward to add on top of the
  existing replay engine (loop `ReplayEngine.run` N times, aggregate
  `ReplayResult.status`), cut for time rather than difficulty.
- **Automatic retry/backoff for transient network failures during replay** -
  currently a slow load either resolves within the fixed timeout or becomes
  a hard failure; a real production version would distinguish "slow" from
  "broken" with a bounded retry, not just a single timeout.

What I'd build next with more time: the agent-facing capability interface
(a small FastAPI endpoint listing artifacts and invoking one by name with
typed args) - it's the most direct path from what's built here to how an AI
agent would actually consume these capabilities in production, and ties
directly into the "capability an AI agent can call" framing the brief uses
throughout.
