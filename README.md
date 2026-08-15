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

<!-- TODO: fill in with exact commands once the agent loop (step 2) exists -->

```bash
# Run the discovery agent on a goal
python -m src.agent.run --goal "look up member 12345 and open a savings sub-account"

# Replay the resulting artifact
python -m src.replay.run --artifact artifacts/open_subaccount.json --member_id 20200 --acct_type Savings --deposit 50
```

## Repo layout

```
target-app/       the mock legacy banking app (target surface)
src/agent/        LLM-driven discovery loop
src/artifacts/    artifact schema + storage
src/replay/       deterministic replay engine
src/escalation/   human-in-the-loop handoff
evidence/         saved artifact + logs from a discovery run and a replay run
REPORT.md         design write-up
```

See `REPORT.md` for architecture, trade-offs, and what was deliberately cut.
