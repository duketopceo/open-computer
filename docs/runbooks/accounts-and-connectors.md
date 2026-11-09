# Accounts and connectors (Composio)

## Accounts

There is no separate password. Signing in is the console's existing gate:

- Behind Cloudflare Access, your Access email is your identity.
- A passkey enrolled on this console also signs you in.
- On loopback (`localhost`), you are `local`.

The first request from an identity creates its account in `OC_ACCOUNTS_PATH` (default `~/.local/share/oc/accounts.json`,
mode 600). The account holds a stable id (`oc_` plus 16 hex of a hash of the identity, never the email), a display name
and a creation time. Settings > Account shows it. **Sign out** clears the console session and then opens Cloudflare Access's
logout page, because the console session would otherwise mint again from the Access cookie.

To add a person, add their email to the Cloudflare Access policy for the site. Their account is created when they first open it.

## Connectors

Off unless the host sets `COMPOSIO_API_KEY`. The key is read from the environment, never logged and never sent to the browser.

| Env | Default | Meaning |
| --- | --- | --- |
| `COMPOSIO_API_KEY` | unset | Composio project API key. Unset: Settings > Connectors says it is off and nothing calls Composio |
| `OC_COMPOSIO_TOOLKITS` | `gmail,googlecalendar,googledrive,github,slack,notion` | Apps users may connect (Composio toolkit slugs) |
| `OC_ACCOUNTS_PATH` | `~/.local/share/oc/accounts.json` | Account records |

How it works:

1. Settings > Connectors lists the apps above and which ones your account has connected (`ACTIVE` connections whose
   Composio user id is your account id).
2. **Connect** opens a Composio session for your account id and asks it for a Connect Link (Composio-managed OAuth).
   You sign in on Composio's page, which sends you back to `/settings?connected=<app>`. Open Computer never sees the
   provider tokens.
3. **Disconnect** deletes that connection in Composio. Only a connection your account owns can be removed.

Using connected apps from a chat is the next step (plan `docs/plans/2026-11-09-001-feat-accounts-connectors-and-chat-polish-plan.md`, U4).
