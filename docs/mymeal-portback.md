# Spec — port Edibl's auth/ingress improvements back into myMeal

**For: myMeal's coding agent, working in the `Amantux/mymeal` repo.**
**Origin:** Edibl mirrored myMeal's add-on↔integration pairing framework, and in
doing so hardened two things beyond the myMeal original. This spec ports those two
deltas back. Everything else in Edibl's auth layer was copied *from* myMeal and is
already identical — do not re-do it (see §0).

> **Ground truth first.** This was written by reading myMeal at a point in time.
> Confirm every cited file/line against the current tree before editing; where
> myMeal has already changed, follow myMeal's actual code, not this document.
> Both ports are **T3** (auth + tenant isolation): add failure-path tests and run
> a reviewer over the diff before declaring done.

---

## §0 — Already aligned (NO action, listed so you don't re-do it)

These exist in both apps and were sourced from myMeal originally. Skip them:

- `load_current_user()` resolution order: Bearer (authoritative in every mode) →
  trusted ingress identity (honored even with auth enabled) → `DISABLE_AUTH`-only
  shared user. myMeal `backend/app/auth.py:166`.
- `_default_user()` / `_ingress_user()` both bind to the **earliest-created**
  Group (`order_by(Group.created_at.asc()).first()`), and `has_owner` counts only
  real HA users (`ha_user_id IS NOT NULL`). myMeal `auth.py:53,109`.
- `integration_token.py` (stable minted key, raw persisted `0600`, hash+hint only)
  and the `/discovery` publisher advertising `{host, port, token}`.

Both ports below are **independent** — apply either or both.

---

## Port 1 — concurrency-safe user provisioning (recommended; fixes a real 500)

### Why
myMeal's `_default_user()` (`auth.py:53-79`) and `_ingress_user()`
(`auth.py:109-163`) each do `db.session.add(user); db.session.commit()` with **no
`IntegrityError` handling**. On a cold instance, several first-load requests race
to create the *same* user: one wins, the rest hit the `UNIQUE` constraint on
`users.email` (or the `ha_user_id`/`email` index) and **500**. This is easy to
trigger the moment a client fires parallel requests on first paint (Edibl hit it
exactly this way when mount fetches were parallelized).

Edibl's fix: let one writer win; the losers roll back and **re-read** the winning
row instead of surfacing the constraint violation.

### What to change — `backend/app/auth.py`

Add `from sqlalchemy.exc import IntegrityError` at the top.

**`_default_user()`** — wrap the create branch (the code after the "user already
exists" early return):

```python
    try:
        group = db.session.query(Group).order_by(Group.created_at.asc()).first()
        if group is None:
            group = Group(name=DEFAULT_GROUP)
            db.session.add(group)
            db.session.flush()
            # ...any per-new-household seeding you already do here...
        user = User(
            name="Local User", email=DEFAULT_EMAIL,
            password_hash=hash_password("unused"), is_owner=True, group_id=group.id,
        )
        db.session.add(user)
        db.session.commit()
        return user
    except IntegrityError:
        # A parallel first-load request created it; re-read the winner's row.
        db.session.rollback()
        return db.session.query(User).filter_by(email=DEFAULT_EMAIL).first()
```

**`_ingress_user()`** — same treatment on the *provision-new-HA-user* commit
(myMeal `auth.py:161-163`):

```python
    db.session.add(user)
    try:
        db.session.commit()
        return user
    except IntegrityError:
        db.session.rollback()
        return db.session.query(User).filter_by(ha_user_id=ha_id).first()
```

**Watch-outs**
- After `rollback()`, the re-read query must run in a clean session — return the
  fetched row directly, don't reuse the rolled-back `user` object.
- Keep any "seed a brand-new household" side effect **inside** the
  `if group is None:` block, so joining an existing group never re-seeds.

### Tests (`backend/tests/`)
- Two threads / two test-client requests racing `_default_user` resolve to the
  **same** user id, and neither 500s. (A direct unit test that calls the create
  branch twice with a pre-inserted row also works and is simpler/deterministic
  than real threads.)
- Same for `_ingress_user` with one `X-Remote-User-Id` from the Supervisor peer.

---

## Port 2 — spoof-proof ingress via the pre-proxy peer (optional; capability + hardening)

### Why
myMeal's `_request_from_ingress()` (`auth.py:96-106`) trusts `X-Remote-User-*`
when `request.remote_addr == "172.30.32.2"`, and **bails out entirely** when
`TRUSTED_PROXY_COUNT > 0`. Consequences of the current design:

1. **Ingress and a trusted proxy are mutually exclusive.** Setting
   `TRUSTED_PROXY_COUNT` (to get correct client IPs / `X-Forwarded-Proto` for
   secure-cookie/HSTS behavior) makes `ProxyFix` rewrite `request.remote_addr`
   from the client-supplied `X-Forwarded-For` — so myMeal *has* to disable ingress
   trust there, or a forged `X-Forwarded-For: 172.30.32.2` would authenticate.
   Today that's safe only because the add-on leaves `TRUSTED_PROXY_COUNT=0` under
   ingress. It's a latent footgun: anyone who enables the proxy count "to fix
   client IPs" silently kills per-user ingress identity.
2. There is **no way** to run ingress behind an extra proxy at all.

Edibl reads the **original TCP peer** that `ProxyFix` stashes *before* it rewrites
`REMOTE_ADDR`, so the check is immune to `X-Forwarded-For` spoofing and works
whether or not `ProxyFix` is installed. Verified against installed werkzeug
(`ProxyFix.__call__` unconditionally writes `environ["werkzeug.proxy_fix.orig"]
["REMOTE_ADDR"]` = the real peer before applying XFF). This lets the add-on run
ingress **and** a trusted proxy simultaneously and removes the footgun.

### What to change — `backend/app/auth.py`

```python
def _raw_peer():
    """The true TCP peer, before ProxyFix rewrote remote_addr from X-Forwarded-For."""
    orig = request.environ.get("werkzeug.proxy_fix.orig")
    if orig and orig.get("REMOTE_ADDR"):
        return orig["REMOTE_ADDR"]
    return request.remote_addr


def _request_from_ingress() -> bool:
    # Read the UNPROXIED peer, so a client-supplied X-Forwarded-For can't spoof the
    # Supervisor address even when ProxyFix is active. This makes the previous
    # "TRUSTED_PROXY_COUNT>0 → distrust ingress" bail unnecessary.
    return _raw_peer() == _INGRESS_SOURCE
```

Drop the `settings.TRUSTED_PROXY_COUNT` early-return from
`_request_from_ingress()`. (If you'd rather keep an explicit kill-switch, gate it
on a *dedicated* setting, not the proxy count — conflating "a proxy exists" with
"don't trust ingress" is the thing being fixed.)

**Do NOT** change the `_INGRESS_SOURCE = "172.30.32.2"` constant or the
"specific host, never the /23" reasoning — that boundary is unchanged.

### Add-on wiring (only if you want ingress-behind-proxy in production)
Optional: set `MYMEAL_TRUSTED_PROXY_COUNT=1` in the add-on's `run.sh` so `ProxyFix`
runs under ingress and `request.remote_addr` / `wsgi.url_scheme` reflect the real
client. With Port 2 this no longer disables ingress identity. Leave it unset to
keep today's behavior — Port 2's code is safe either way.

### Tests (`backend/tests/`)
1. **XFF-spoof rejected (the load-bearing test):** app with
   `TRUSTED_PROXY_COUNT=1`, real TCP peer = a LAN address, header
   `X-Forwarded-For: 172.30.32.2` + `X-Remote-User-Id: ...` → **401**. This fails
   loudly if anyone reverts `_raw_peer()` to `request.remote_addr`.
2. **Ingress still honored under ProxyFix:** `TRUSTED_PROXY_COUNT=1`, real TCP
   peer = `172.30.32.2` → identity resolved, **200**.
3. **Ingress honored with no proxy:** `TRUSTED_PROXY_COUNT=0`, peer
   `172.30.32.2` → **200** (regression guard for the `_raw_peer` fallback path).

Flask test client: set the true peer via `environ_overrides={"REMOTE_ADDR": ...}`
and forge the header via `headers={"X-Forwarded-For": "172.30.32.2"}`.

---

## Verify (both ports)
- `ruff check .` clean; full `pytest` green from `backend/`.
- Port 1: the race tests 200 (not 500) and resolve one identity.
- Port 2: the three ingress tests above; and manually confirm the add-on's
  existing ingress users are unaffected when `TRUSTED_PROXY_COUNT` stays 0.
- Run a reviewer over the diff (T3: auth + tenant isolation).

## Reference (Edibl's shipped versions to mirror)
- `backend/app/auth.py` — `_raw_peer`, `_request_from_ingress`, the
  `IntegrityError` guards in `_default_user`/`_ingress_user`.
- `backend/tests/test_integration_auth.py` —
  `test_forged_xforwarded_for_supervisor_rejected` is the Port-2 XFF test.
- `backend/tests/test_ingress_auth.py` — the per-user ingress-identity suite.
