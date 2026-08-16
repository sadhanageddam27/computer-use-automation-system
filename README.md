# Computer-Use Automation System

An LLM discovers how to complete a goal against a legacy UI with no API, records
the successful run as a typed, reusable artifact, and replays that artifact
deterministically afterward with no model in the decision loop.

Built for the interface.ai take-home assignment.

## Setup

```bash
git clone <this repo>
cd computer-use-automation-system
pip install -r requirements.txt
playwright install chromium
```

Requires an Anthropic API key:

```bash
export ANTHROPIC_API_KEY=sk-...
```

### Run the target app

The target surface is a deliberately legacy-styled mock banking app (see
`target-app/README.md` for details, seed data, and error-injection hooks).

```bash
cd target-app
python app.py
```

Runs at http://localhost:5001

## Demo path

```bash
# 1. Run the discovery agent on a goal (needs ANTHROPIC_API_KEY set)
python -m src.agent.run \
  --goal "Log in and open a savings sub-account for member 12345" \
  --member-id 12345 --acct-type Savings --deposit 100

# 2. Build a reusable artifact from the resulting discovery log
python -m src.artifacts.build_from_log \
  --log evidence/<the discovery log just written> \
  --name open_member_subaccount \
  --param member_id=12345 --param acct_type=Savings --param deposit=100 \
  --allow "http://localhost:5001/*"

# 3. Replay the artifact deterministically - no LLM involved
python -m src.replay.run \
  --artifact artifacts/open_member_subaccount_v1.json \
  --input member_id=12345 --input acct_type=Savings --input deposit=100 --input password=x \
  --auto-confirm-risky   # omit this flag to get a real interactive confirmation prompt
```

No API key is needed for steps 2 and 3, or to test step 1's harness end to end via
`python -m src.agent.run --dry-run ...` (uses a scripted decider instead of Claude).

## Repo layout

```
target-app/       the mock legacy banking app (target surface)
src/agent/        LLM-driven discovery loop
src/artifacts/    artifact schema + storage
src/replay/       deterministic replay engine
src/escalation/   human-in-the-loop handoff
src/safety/       allowlist enforcement + secret redaction
artifacts/        saved capability artifacts
evidence/         saved artifact + logs from a discovery run and a replay run
REPORT.md         design write-up
```

See `REPORT.md` for architecture, trade-offs, and what was deliberately cut.
