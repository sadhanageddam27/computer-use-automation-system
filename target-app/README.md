# Legacy Core Banking Mock

Stand-in target surface for the interface.ai computer-use assignment. Models an
internal member-servicing tool the way the brief describes: server-rendered,
no clean DOM, no test IDs, session-gated.

## Run

```
pip install -r requirements.txt
python app.py
```

Visit http://localhost:5001/ — log in with any non-empty username/password.

## Seed data

| Member ID | Notes                          |
|-----------|---------------------------------|
| 12345     | Normal member, balance $4210.55 |
| 20200     | Normal member, balance $980.10  |
| 99999     | Restricted — returns 403        |

## The flow to automate

Search -> member detail -> open sub-account (type + initial deposit) ->
confirmation screen showing the new sub-account ID. This is the multi-step
"search -> detail -> action with confirmation" pattern the brief asks for.

## Deliberate legacy properties

- Nested `<table>` layout, `<font>` tags, no semantic HTML5 elements
- No `id`, `data-*`, or other test-hook attributes anywhere
- Unstable-looking generated class names (`tbl-a11c`, `tbl-f77a`, ...) that
  should NOT be relied on as a locator strategy
- Login/session gate models real session/timeout expiry

## Deterministic error-injection hooks

These exist so the discovery agent and, more importantly, the **replay
engine** can be exercised against real runtime conditions reproducibly,
instead of relying on flaky timing:

| Query param            | Where                              | Simulates                        |
|-------------------------|-------------------------------------|-----------------------------------|
| `?inject_expired=1`     | any `@login_required` route         | session/timeout expiry            |
| `?inject_dialog=1`      | POST to `/member/<id>/subaccount/new` | unexpected confirmation dialog  |
| `?inject_slow=1`        | `/member/<id>/slow`                 | slow/failed load                  |

Built-in business outcomes (no injection needed):
- Search for a nonexistent member ID -> "no record found"
- Search for member `99999` -> permission denied (403)
- Deposit greater than balance, or non-numeric deposit -> validation error
