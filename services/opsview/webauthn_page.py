"""Server-rendered WebAuthn passkey pages (sign-in + one-time enrollment).

Pure stdlib, no framework, no build step, no external resources.
"""

import json

_CSS = r"""html,body{background:var(--color-canvas);color:var(--color-ink);margin:0}
body{font:var(--text-body)/1.5 var(--font-ui)}
main{max-width:380px;margin:18vh auto;padding:0 var(--space-16)}
h1{font-size:var(--text-title);font-weight:500;margin:0 0 var(--space-24)}
#status{min-height:1.5em}
.w{font-weight:600}
.ok,.bad,.pend{color:var(--color-ink)}
.ok::before{content:"\2713 "}.bad::before{content:"\2715 "}.pend::before{content:"! "}
.dim{color:var(--color-ink-subtle)}
button{margin-top:var(--space-16);min-height:var(--row-compact);background:var(--color-accent);color:var(--color-on-accent);border:1px solid var(--color-accent);border-radius:var(--radius-sm);font:inherit;font-weight:500;padding:0 var(--space-12);cursor:pointer}
button:hover{background:var(--color-accent-hover)}
a{color:var(--color-accent)}
:focus-visible{outline:2px solid var(--color-accent);outline-offset:2px}
#done{margin-top:var(--space-16)}
[hidden]{display:none !important}
"""

# Shared helpers. Plain string (not f-string) so JS braces are untouched.
_JS = r"""
const $ = (id) => document.getElementById(id);

// websafe base64url -> ArrayBuffer (accepts padded or unpadded input)
function b64uToBuf(s) {
  s = String(s).replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  const bin = atob(s);
  const u = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
  return u.buffer;
}

// ArrayBuffer -> unpadded base64url
function bufToB64u(buf) {
  const u = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function setStatus(word, cls, msg) {
  $('word').className = 'w ' + cls;
  $('word').textContent = word;
  $('msg').textContent = msg || '';
}

function showRetry(on) { $('retry').hidden = !on; }

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  let data = null;
  try { data = await r.json(); } catch (e) { /* non-JSON body */ }
  if (!r.ok || !data) {
    throw new Error((data && data.error) || ('request failed (HTTP ' + r.status + ')'));
  }
  return data;
}

const NO_WEBAUTHN = 'passkey required — no webauthn in this browser';
"""

_LOGIN_JS = r"""
function login() {
  if (!window.PublicKeyCredential || !navigator.credentials || !navigator.credentials.get) {
    setStatus('DENIED', 'bad', NO_WEBAUTHN);
    showRetry(false);
    return;
  }
  setStatus('AUTHENTICATING', 'pend', 'touch your passkey');
  showRetry(false);
  (async () => {
    try {
      const opts = await postJSON('/webauthn/login/begin', {});
      const pk = opts.publicKey;
      pk.challenge = b64uToBuf(pk.challenge);
      if (pk.allowCredentials) {
        pk.allowCredentials = pk.allowCredentials.map(
          (c) => Object.assign({}, c, {id: b64uToBuf(c.id)}));
      }
      const cred = await navigator.credentials.get({publicKey: pk});
      const res = cred.response;
      const credential = {
        id: cred.id,
        rawId: bufToB64u(cred.rawId),
        type: cred.type,
        response: {
          clientDataJSON: bufToB64u(res.clientDataJSON),
          authenticatorData: bufToB64u(res.authenticatorData),
          signature: bufToB64u(res.signature),
          userHandle: res.userHandle ? bufToB64u(res.userHandle) : null,
        },
      };
      await postJSON('/webauthn/login/complete', {tok: opts.tok, credential: credential});
      location.reload();
    } catch (e) {
      setStatus('DENIED', 'bad', (e && e.message) || 'authentication failed');
      showRetry(true);
    }
  })();
}

$('retry').onclick = () => login();
login();
"""

_ENROLL_JS = r"""
const TOKEN = __TOKEN_JSON__;

function enroll() {
  if (!window.PublicKeyCredential || !navigator.credentials || !navigator.credentials.create) {
    setStatus('DENIED', 'bad', NO_WEBAUTHN);
    showRetry(false);
    return;
  }
  setStatus('ENROLL', 'pend', 'touch your passkey to register');
  showRetry(false);
  $('done').hidden = true;
  (async () => {
    try {
      const opts = await postJSON('/webauthn/register/begin', {token: TOKEN});
      const pk = opts.publicKey;
      pk.challenge = b64uToBuf(pk.challenge);
      pk.user.id = b64uToBuf(pk.user.id);
      if (pk.excludeCredentials) {
        pk.excludeCredentials = pk.excludeCredentials.map(
          (c) => Object.assign({}, c, {id: b64uToBuf(c.id)}));
      }
      const cred = await navigator.credentials.create({publicKey: pk});
      const res = cred.response;
      const credential = {
        id: cred.id,
        rawId: bufToB64u(cred.rawId),
        type: cred.type,
        response: {
          clientDataJSON: bufToB64u(res.clientDataJSON),
          attestationObject: bufToB64u(res.attestationObject),
        },
      };
      await postJSON('/webauthn/register/complete', {tok: opts.tok, credential: credential});
      setStatus('ENROLLED', 'ok', 'passkey registered');
      $('done').hidden = false;
    } catch (e) {
      setStatus('DENIED', 'bad', (e && e.message) || 'enrollment failed');
      showRetry(true);
    }
  })();
}

$('retry').onclick = () => enroll();
enroll();
"""

_LOGIN_BODY = ""

_ENROLL_BODY = (
    '<div id="done" hidden>enrolled — '
    '<a href="/">continue to sign in</a></div>'
)


def _page(title: str, body: str, script: str) -> str:
    return (
        '<!doctype html>\n'
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex">'
        '<title>open-computer · ' + title + '</title>'
        '<link rel="stylesheet" href="/static/silver-lining.css"><style>' + _CSS + '</style></head>\n'
        '<body><main>'
        '<h1>open-computer</h1>'
        '<div id="status"><span id="word"></span> <span id="msg" class="dim"></span></div>'
        '<button id="retry" type="button" hidden>retry</button>'
        + body +
        '</main>\n'
        '<script>' + _JS + script + '</script>\n'
        '</body></html>\n'
    )


def login_page() -> str:
    """Render the sign-in page (assertion ceremony)."""
    return _page("sign in", _LOGIN_BODY, _LOGIN_JS)


def enroll_page(token: str) -> str:
    """Render the one-time enrollment page (registration ceremony).
    `token` is a URL-supplied single-use enroll token — embed it in the JS."""
    # json.dumps gives a valid JS string literal; additionally escape
    # HTML-significant characters so the value can never close the <script>.
    token_js = (
        json.dumps(str(token))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    script = _ENROLL_JS.replace("__TOKEN_JSON__", token_js)
    return _page("enroll", _ENROLL_BODY, script)
