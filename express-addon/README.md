# Chameleon Adobe Express add-on

This is a manifest-v2 panel with a document sandbox, using Adobe's UI SDK and
Document SDK. It shares the studio's interface and local Python engine.
The add-on imports Chameleon JSON and matching image assets, previews reflow,
exports PNG/JSON/campaign ZIP, and inserts the chosen PNG onto a **new page at
the exact target dimensions**. The inserted PNG is a single image; native
Express text/layer editing and reading an arbitrary existing Express page
are not implemented. JSON preserves the element-level layout data.

## Build and package

Node 22+ and Python 3.12+:

```sh
cd express-addon
npm ci
npm test
npm run build -- --analytics off
npm run package -- --analytics off
```

The official Adobe CLI validates the manifest during the build. The Python
release packager also creates `artifacts/chameleon-express-addon.zip` with
`manifest.json` at its root.

## Run inside Express

Use an existing **trusted localhost certificate and key**. Adobe's official
local SSL setup tool can create these if you do not have them:

```sh
npx @adobe/ccweb-add-on-ssl setup --hostname localhost
```

Certificate trust and any operating-system password prompt must be completed
by the developer. Do not disable browser certificate validation.

From the repository root, run the studio with the certificate paths printed
by the SSL setup:

```sh
python -m studio.app --port 5241 --addon --cert /path/to/localhost.crt --key /path/to/localhost.key
```

The Python development server implements the same add-on listing/resource
endpoints as Adobe's CLI, and serves the engine API on that same HTTPS origin.
The studio is available at https://localhost:5241/studio. There is no hot reload;
reload the add-on after code changes.

In Adobe Express, enable Add-on Development in your developer settings, open
the Add-on Development panel, and connect to **https://localhost:5241**. Follow
Adobe's current sideloading instructions if the UI changes. Allow local-network
access when Chrome explicitly asks for it. No remote model or API key is used.

## Host acceptance checklist

- Load the panel inside a signed-in Adobe Express account.
- Select a sample or import Chameleon JSON and matching images.
- Reflow and verify the preview and displayed warning list.
- Insert a new Express page; verify its size and the original page is intact.
- Verify downloads work and undo removes the newly inserted page.
- Repeat at the normal Express sidebar width.

The manifest build and sandbox unit tests have been checked locally. Live
host acceptance requires the developer's signed-in account and trusted local
certificate; it must be recorded separately before claiming finale readiness.

Official references:
- https://developer.adobe.com/express/add-ons/docs/references/manifest/
- https://developer.adobe.com/express/add-ons/docs/references/document-sandbox/
- https://developer.adobe.com/express/add-ons/docs/references/document-sandbox/document-apis/classes/page-list
- https://developer.adobe.com/express/add-ons/docs/references/document-sandbox/document-apis/classes/editor
