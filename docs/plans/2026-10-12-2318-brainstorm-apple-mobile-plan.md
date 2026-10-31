---
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-brainstorm
date: 2026-10-12
---

# OC Mobile — native iOS surface for the orchestrator

## Goal Capsule

An Apple Developer account just landed. Build a SwiftUI iOS app that puts
open-computer in your pocket — **approvals first, then monitoring, then
intake** — so the orchestrator's human bottleneck (blocked runs awaiting
a decision) reaches you wherever you are, not just at the console.

Devices in play: iPhone (daily), iPad, a Mac running macOS (the Xcode
build/sign machine — this Asahi box writes code, the Mac ships it).
No watchOS in phase one.

## Product Contract

### Why now / verified context

- The workplace console (oc.example.com) is live: goals → subtasks →
  route → place → gate → sandbox → audit, Temporal durable drive, approval
  queue with hash-bound decisions, passkey + Cloudflare Access perimeter.
- The one latency that matters in the system is **human**: a run parked
  in AWAITING-APPROVAL dies on the vine when you're off-console.
- Paid dev account unlocks real APNs, notification actions, associated
  domains (native passkeys + universal links), share extensions, widgets,
  Live Activities, App Intents/Siri, TestFlight.

### Phase M1 — Approvals (the spine)

- APNs push when a task lands in the approval queue — deep link into the run.
- **Notification actions** (Approve / Reject / Needs-info) that work from
  the notification itself — same hash-bound decision the console makes.
- App: passkey sign-in via `ASAuthorizationPlatformPublicKeyCredential`
  against `oc.example.com` (associated domains + the AASA file served
  by opsview), approval queue list, run detail.
- Server side (new `services/push/`): device-token registry, APNs JWT
  (.p8 key in omaseal `apple/apns-key`), gate→push hook emitting
  "approval needed" with run id + short title only — never task body.
- Small JSON API layer on opsview: `/api/runs`, `/api/run/<id>`,
  `/api/approvals`, device registration — session-authed, same origin +
  CSRF rules as every other route. The console already has `POST /goals`.

### Phase M2 — Monitor

- Run list + live detail in-app (poll; no push needed for status).
- Widgets: run-status glance, pending-approval count.
- Live Activity for in-flight runs (lock screen / Dynamic Island).

### Phase M3 — Intake

- Share Sheet extension: URL / text / file → POST /goals as a run.
- App Intents + Siri phrase: "run a goal" with dictated text.
- Background submission, result notification on completion.

### Non-goals (phase one)

- No macOS Catalyst/Mac app — the console already lives on the laptop.
- No watchOS, no Android, no App Store review (TestFlight ad-hoc for
  your own devices is plenty for a personal surface).
- No multi-user push — one Apple ID, your registered devices.
- No replacing Cloudflare Access — the app still passes the perimeter;
  the service-token policy exists exactly for this class of client.

### Success criteria

- A gated subtask on a real run produces a lock-screen notification
  inside ~seconds, and Approve from the notification advances the run —
  verified end-to-end on your iPhone.
- Queue/count parity between app and console (same disk truth).
- Push payload leaks nothing sensitive: run id + title only.
- Works through the existing Access + passkey + CSRF chain — no new
  auth bypass for the API surface.

### Constraints

- Dev account is new: APNs key (.p8), app id, AASA hosting, entitlement
  setup (push, associated domains, time-sensitive notifications) all
  first-time plumbing — budget the yak-shave in plan.
- Builds/signing need the Mac (Xcode); code can be authored here and
  synced via git. GitHub macOS runners are the fallback CI path.
- `temporalio` + the service env live on this box; the push service
  runs where opsview runs.

### Open decisions for ce-plan

- Device-token registry shape: fsproto dir vs `~/.local/share/oc/devices.json`.
- Notification payload policy: title-only vs redacted-class.
- Whether notification actions mutate via the existing approvals POST
  (preferred — same audit) or a dedicated device-action endpoint.
- App architecture: single-target app + share extension target; widget
  extension added in M2.
