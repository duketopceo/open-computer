# Console browser tests

Playwright + axe-core against fixture data (`tests/opsview/serve_fixture.py`): no microVMs,
no models, no network beyond 127.0.0.1. The fixture swaps the matrix runner for a stub and
uses a fake passkey session (`oc_sess=test`), so it is test-only.

    cd tests/e2e && npm i && npx playwright install chromium && npm test
    npm run test:a11y        # axe, WCAG 2.2 AA, both themes, 1440 and 390, every route
    npm run test:keyboard    # keyboard-only journeys

Already have playwright and axe-core somewhere? Skip npm:

    NODE_PATH=/path/to/node_modules AXE_JS=/path/to/axe.min.js node --test tests/e2e/console.e2e.mjs

`OC_PYTHON` overrides the fixture interpreter (default `uv run --with fido2 python`).

Screenshots: start the fixture (`python -I tests/opsview/serve_fixture.py 8851 /tmp/fx`), then
`node tests/e2e/shots.mjs http://127.0.0.1:8851 docs/screenshots/console after`.

Python tests (routing, side-effect-free GETs, CSRF, approvals, registry, tokens):

    uv run --with pytest --with fido2 pytest tests/opsview

## Agent workspace

`workspace.e2e.mjs` drives the workspace (`/workspace`) against the replay fixture (`serve_fixture.py --replay`):
no sandbox, no model, only 127.0.0.1. Each test file starts its own fixture servers on its own ports.

    npm run test:workspace   # replay determinism, approvals, live stability, keyboard, palette, citations, cap, phone, layout
    npm run test:replay      # same recorded stream twice (and paced vs instant) gives identical DOM snapshots
    npm run test:approval    # no reuse after a payload change, Esc never decides, high risk needs two presses
    npm run test:live        # a 200-event burst keeps scroll, tab, focus and the hovered row

Gates (their own file and fixture servers): `npm run test:gates`

    npm run test:artifacts   # every viewer renders its fixture with axe at 0 violations; the HTML viewer has no network
    npm run test:a11y        # (also the console) axe on every tab and state, both themes, 1440 and 390, incl. approval cards

Screenshots: start the fixture (`python -I tests/opsview/serve_fixture.py 8871 /tmp/fx --replay`), then
`node tests/e2e/workspace-shots.mjs http://127.0.0.1:8871 docs/screenshots/workspace`.

See [docs/design/agent-workspace.md](../../docs/design/agent-workspace.md) and
[docs/design/event-stream.md](../../docs/design/event-stream.md).
