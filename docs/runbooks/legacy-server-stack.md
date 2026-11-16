# Legacy server stack (retired)

The Python stack (console, Temporal worker, Postgres) used to deploy to the owner's server over SSH from the CI
`deploy (production)` job. The Cloudflare core replaces it, so that job is off by default.

## State when retired

- The CI job only runs when the repository variable `OC_LEGACY_DEPLOY` is `true`. It is unset, so pushes to `main` no
  longer deploy to the server.
- On the server, no `oc` compose project, no `oc-*` containers and no `oc-*` volumes were present when this was
  checked, and the checkout directory did not exist. Nothing needed stopping; no data was touched and no other
  application on the server was touched.

## Restart it (if ever needed)

```sh
# 1. allow the CI job again
gh variable set OC_LEGACY_DEPLOY --body true
# 2. or run it by hand on the server, as the deploy user
git clone https://github.com/duketopceo/open-computer.git ~/open-computer
cd ~/open-computer && deploy/server-001/redeploy.sh "$(git rev-parse HEAD)"
```

## Stop it (if it is running somewhere)

```sh
cd ~/open-computer
docker compose -p oc -f deploy/server-001/docker-compose.oc.yml --env-file ~/.config/oc/server-001.env stop
# Use `stop`, or `down` without `-v`. Never pass `-v`: the oc-data and oc-pgdata volumes hold passkeys, runs and audit.
```

Data stays in the `oc-data` and `oc-pgdata` volumes until you delete them yourself.
