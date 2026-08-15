"""
Legacy Core Banking Mock (interface.ai take-home target surface)

Deliberately styled like a legacy internal servicing tool:
- server-rendered HTML, no client-side JS
- nested <table> layouts instead of semantic markup
- no test IDs / data-* attributes
- unstable, generated-looking CSS class names
- a login/session gate to model session/timeout expiry
- built-in, deterministic error-injection hooks (query params) so the
  discovery agent and the replay engine can both be exercised against
  real runtime error conditions, not just the happy path.

Run:
    pip install flask
    python app.py
Then visit http://localhost:5001/
"""

import random
import string
import time
from functools import wraps

from flask import Flask, request, redirect, url_for, session, render_template_string

app = Flask(__name__)
app.secret_key = "dev-only-not-a-real-secret"

# ---------------------------------------------------------------------------
# In-memory "core banking" data
# ---------------------------------------------------------------------------

MEMBERS = {
    "12345": {"name": "Alice Whitfield", "balance": 4210.55, "restricted": False},
    "20200": {"name": "Marcus Reyes", "balance": 980.10, "restricted": False},
    "99999": {"name": "Restricted Account Holder", "balance": 0.0, "restricted": True},
}

SUBACCOUNT_TYPES = ["Savings", "Checking", "CD-12mo"]

# member_id -> list of subaccount dicts
SUBACCOUNTS = {}


def _gen_subaccount_id():
    return "".join(random.choices(string.digits, k=8))


# ---------------------------------------------------------------------------
# Session gate (models session/timeout expiry the way the real environment
# would produce it)
# ---------------------------------------------------------------------------

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Deterministic error-injection hook: ?inject_expired=1 forces a
        # session-expiry condition on this request, regardless of actual
        # session state, so the replay engine can be tested against it
        # reliably instead of relying on real clock timing.
        if request.args.get("inject_expired") == "1":
            session.clear()

        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Shared legacy-style layout helpers
# ---------------------------------------------------------------------------

BASE = """
<html><head><title>{{ title }}</title></head>
<body bgcolor="#ffffff">
<table width="640" border="0" cellpadding="0" cellspacing="0" class="tbl-x9f2">
<tr><td>
<table width="100%" border="1" cellpadding="6" cellspacing="0" class="tbl-a11c">
<tr bgcolor="#dcdcdc"><td colspan="2"><font size="+1">Member Servicing Console</font></td></tr>
<tr><td colspan="2">{{ body|safe }}</td></tr>
</table>
</td></tr>
</table>
</body></html>
"""


def render(title, body):
    return render_template_string(BASE, title=title, body=body)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username and password:
            session["user"] = username
            nxt = request.args.get("next") or url_for("index")
            return redirect(nxt)
        error = "Username and password are required."

    body = """
    <table cellpadding="4" cellspacing="0" class="tbl-c02d">
      <tr><td colspan="2"><b>Operator Login</b></td></tr>
      %s
      <form method="post">
      <tr><td>Username</td><td><input type="text" name="username"></td></tr>
      <tr><td>Password</td><td><input type="password" name="password"></td></tr>
      <tr><td colspan="2"><input type="submit" value="Log In"></td></tr>
      </form>
    </table>
    """ % (f'<tr><td colspan="2"><font color="red">{error}</font></td></tr>' if error else "")
    return render("Login", body)


@app.route("/")
@login_required
def index():
    body = """
    <table cellpadding="4" cellspacing="0" class="tbl-f77a">
      <tr><td colspan="2"><b>Member Lookup</b></td></tr>
      <form method="post" action="/search">
      <tr><td>Member ID</td><td><input type="text" name="member_id"></td></tr>
      <tr><td colspan="2"><input type="submit" value="Search"></td></tr>
      </form>
    </table>
    """
    return render("Member Lookup", body)


@app.route("/search", methods=["POST"])
@login_required
def search():
    member_id = request.form.get("member_id", "").strip()
    member = MEMBERS.get(member_id)

    if not member:
        body = f"""
        <table cellpadding="4" class="tbl-d44e">
          <tr><td><font color="red">No record found for member ID "{member_id}".</font></td></tr>
          <tr><td><a href="/">Back to search</a></td></tr>
        </table>
        """
        return render("Not Found", body)

    if member["restricted"]:
        body = f"""
        <table cellpadding="4" class="tbl-d44e">
          <tr><td><font color="red">Access to member {member_id} is restricted for this operator.</font></td></tr>
          <tr><td><a href="/">Back to search</a></td></tr>
        </table>
        """
        return render("Permission Denied", body), 403

    return redirect(url_for("member_detail", member_id=member_id))


@app.route("/member/<member_id>")
@login_required
def member_detail(member_id):
    member = MEMBERS.get(member_id)
    if not member:
        return redirect(url_for("index"))

    subaccounts = SUBACCOUNTS.get(member_id, [])
    rows = "".join(
        f'<tr><td>{s["id"]}</td><td>{s["type"]}</td><td>${s["deposit"]:.2f}</td></tr>'
        for s in subaccounts
    ) or '<tr><td colspan="3"><i>None</i></td></tr>'

    body = f"""
    <table cellpadding="4" class="tbl-e91b">
    <tr><td colspan="2"><b>Member Detail</b></td></tr>
    <tr><td>Name</td><td>{member['name']}</td></tr>
    <tr><td>Member ID</td><td>{member_id}</td></tr>
    <tr><td>Current Balance</td><td>${member['balance']:.2f}</td></tr>
    </table>
    <br>
    <table border="1" cellpadding="4" class="tbl-b30a">
    <tr bgcolor="#eeeeee"><td>Sub-Account ID</td><td>Type</td><td>Initial Deposit</td></tr>
    {rows}
    </table>
    <br>
    <a href="/member/{member_id}/subaccount/new">Open new sub-account</a>
    """
    return render(f"Member {member_id}", body)


@app.route("/member/<member_id>/subaccount/new", methods=["GET", "POST"])
@login_required
def new_subaccount(member_id):
    member = MEMBERS.get(member_id)
    if not member:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        # Deterministic error-injection hook: ?inject_dialog=1 forces an
        # extra confirmation interstitial before the action completes,
        # to exercise "unexpected confirmation dialog" handling.
        if request.args.get("inject_dialog") == "1" and not request.form.get("confirmed"):
            body = f"""
            <table cellpadding="4" class="tbl-dlg1">
              <tr><td colspan="2"><b>Are you sure?</b></td></tr>
              <tr><td colspan="2">This will open a new sub-account for member {member_id}.</td></tr>
              <form method="post" action="/member/{member_id}/subaccount/new?inject_dialog=1">
              <input type="hidden" name="acct_type" value="{request.form.get('acct_type','')}">
              <input type="hidden" name="deposit" value="{request.form.get('deposit','')}">
              <input type="hidden" name="confirmed" value="1">
              <tr><td colspan="2"><input type="submit" value="Yes, proceed"></td></tr>
              </form>
            </table>
            """
            return render("Confirm", body)

        acct_type = request.form.get("acct_type", "")
        deposit_raw = request.form.get("deposit", "")

        try:
            deposit = float(deposit_raw)
        except ValueError:
            deposit = None

        if acct_type not in SUBACCOUNT_TYPES:
            error = "Invalid account type."
        elif deposit is None or deposit < 0:
            error = "Initial deposit must be a non-negative number."
        elif deposit > member["balance"]:
            error = "Initial deposit exceeds available balance."
        else:
            sub_id = _gen_subaccount_id()
            SUBACCOUNTS.setdefault(member_id, []).append(
                {"id": sub_id, "type": acct_type, "deposit": deposit}
            )
            return redirect(url_for("confirm_subaccount", member_id=member_id, subaccount_id=sub_id))

    options = "".join(f'<option value="{t}">{t}</option>' for t in SUBACCOUNT_TYPES)
    body = f"""
    <table cellpadding="4" class="tbl-nw88">
      <tr><td colspan="2"><b>Open Sub-Account for {member_id}</b></td></tr>
      {f'<tr><td colspan="2"><font color="red">{error}</font></td></tr>' if error else ''}
      <form method="post">
      <tr><td>Account Type</td><td><select name="acct_type">{options}</select></td></tr>
      <tr><td>Initial Deposit</td><td><input type="text" name="deposit"></td></tr>
      <tr><td colspan="2"><input type="submit" value="Open Account"></td></tr>
      </form>
    </table>
    """
    return render("New Sub-Account", body)


@app.route("/member/<member_id>/subaccount/<subaccount_id>/confirm")
@login_required
def confirm_subaccount(member_id, subaccount_id):
    subs = SUBACCOUNTS.get(member_id, [])
    match = next((s for s in subs if s["id"] == subaccount_id), None)
    if not match:
        return redirect(url_for("member_detail", member_id=member_id))

    body = f"""
    <table cellpadding="4" class="tbl-conf3">
      <tr><td colspan="2"><b>Sub-account opened successfully.</b></td></tr>
      <tr><td>Sub-Account ID</td><td>{match['id']}</td></tr>
      <tr><td>Type</td><td>{match['type']}</td></tr>
      <tr><td>Initial Deposit</td><td>${match['deposit']:.2f}</td></tr>
      <tr><td colspan="2"><a href="/member/{member_id}">Back to member</a></td></tr>
    </table>
    """
    return render("Confirmation", body)


# Deterministic "slow/failed load" injection for testing recoverable conditions
@app.route("/member/<member_id>/slow")
@login_required
def slow_endpoint(member_id):
    if request.args.get("inject_slow") == "1":
        time.sleep(3)
    return redirect(url_for("member_detail", member_id=member_id))


if __name__ == "__main__":
    app.run(port=5001, debug=True)
