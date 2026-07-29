# Access: who can see the board

Two layers, and only one of them is real security.

| Layer | What it does | What it protects |
|---|---|---|
| **Cloudflare Access** | Blocks unauthenticated requests at the edge | **Everything** — HTML, JSON, all of it |
| **Board sign-in** | Keeps the dashboard from opening | The rendered page only |

## Read this before trusting the sign-in

The board is a static site. The sign-in screen is JavaScript running in the visitor's browser, which
means it can decide whether to *render* the dashboard but cannot decide whether a file is *served*.
Anyone holding the URL can fetch these directly, signed in or not:

```
/data/dashboard-data.json     every floor, client and number metric
/data/client-revenue.json     per-client MRR, ARR and deal counts
/config/access.json           the account list (salts and hashes, no passwords)
/config/dashboard-config.json targets and thresholds
```

That is not a flaw in how the sign-in was built; it is what a static site is. No amount of
client-side code changes it. So:

> **Until Cloudflare Access is switched on, treat the URL as public and do not forward it.**

This is also what `docs/compliance.md` already requires: *"The board is never on a public URL.
Cloudflare Pages plus Cloudflare Access, allowlist by email domain plus named external addresses. No
'unlisted URL' as a security model, ever. An unlisted URL is a public URL that has not been found
yet."*

## Turning on Cloudflare Access (about two minutes)

1. Cloudflare dashboard → **Zero Trust** → **Access** → **Applications** → **Add an application**
2. Type: **Self-hosted**
3. Application name: `OBB Setter Floor Dashboard`
4. Session duration: **24 hours**
5. Application domain: `db.homecarehero.tech` (leave the path blank so it covers the whole site)
6. **Add policy** → name it `Named accounts`, action **Allow**
7. Include → **Emails** → add:
   - `management@onlinebizbuilders.com`
   - `emonies@onlinebizbuilders.com`
   - `john@fincialsalesinsider.com`
8. Save. Under **Login methods**, **One-time PIN** is enough — Access emails a code, so there is no
   password to manage or rotate.

After that, an unauthenticated request to any path returns the Access login, including the raw JSON
paths above. That is the difference between a gate and a lock.

Adding someone later is one line in the Access policy. It does not require a deploy.

## The board sign-in

Kept because it is useful even with Access on: it names who is looking, drives the admin tab, and
means a shared or unlocked laptop does not sit on an open dashboard.

- **Accounts** live in `config/access.json`.
- **Passwords are never stored.** Each account holds a random 16-byte salt and a PBKDF2-HMAC-SHA256
  derivation of the password at 210,000 iterations. Sign-in recomputes the derivation in the browser
  and compares it in constant time.
- **A forgotten password is reissued, never recovered.** Nothing in the repo or the file can be
  turned back into a password.
- **Sessions last 12 hours** per device, in `localStorage`.
- **Unknown emails still pay the full hashing cost**, so the form cannot be used to work out which
  addresses are accounts.
- **It fails closed.** If `config/access.json` is missing from a deploy, nobody gets in — the board
  refuses to open rather than defaulting to open.

### Roles

| Role | Sees |
|---|---|
| `admin` | Everything, plus the **Admin** tab |
| `viewer` | Everything except the Admin tab |

The Admin tab is appended for admins at render time rather than listed in `dashboard-config.json`,
so a viewer never renders the button at all.

`john@fincialsalesinsider.com` was set to `viewer` because it is outside the
`onlinebizbuilders.com` domain — change `"role"` in `config/access.json` if that is wrong.

### Changing the account list

Edit `config/access.json` and redeploy. To add a user or reset a password, generate a new salt and
hash:

```python
import secrets, hashlib, base64
password = "..."                       # the new password
salt = secrets.token_bytes(16)
dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210000, 32)
print(base64.b64encode(salt).decode(), base64.b64encode(dk).decode())
```

Put those in the user's `salt` and `hash`. Never commit the password itself.
