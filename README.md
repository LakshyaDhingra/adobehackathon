# Chameleon — Any Design. Every Canvas.

A deterministic, offline design reflow engine for the Adobe University
Hackathon. Give it a source design and a target canvas; it returns the same
elements with new geometry and font sizes. The studio adds comparisons,
layout choices, custom JSON/image imports, and campaign exports.

## Run the engine

Use Python **3.12+**, preferably in a fresh virtual environment:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
python -m engine.main
```

Each task in `input/` produces `output/<task>_output.json` and
`output/<task>_output.png`. The PNG uses actual brand fonts and assets through
cr-renderer. Large targets render at their full requested dimensions.
A malformed task fails with a nonzero exit code while other tasks continue;
stale output for failed tasks is removed. A missing renderer is a failure,
never a labelled-box preview masquerading as a scored artifact.

```sh
python -m engine.main --input-dir input --output-dir output
```

The official `resize(source, target_canvas)` interface is in
[`engine/resize.py`](engine/resize.py). Extra optional arguments are for the
studio; scoring uses the standard two arguments.

## Run the studio

```sh
python -m studio.app
```

Open **http://localhost:5050**. Choose a sample or import Chameleon JSON, add
matching PNG/JPEG/WebP assets, pick a target, and reflow. Compare with the
original, inspect warnings, choose a layout direction, and download PNG/JSON.
**Export campaign** creates four sizes plus a quality report in one ZIP.

The studio and scoring engine preserve every source element and word. No
hidden copy shortening, opacity-based removal, or regenerated image content
is used. A missing starter asset remains an explicit placeholder warning.

## Container submission

```sh
docker build --platform linux/amd64 -t chameleon .
docker run --rm --platform linux/amd64 --network none \
  -v "$(pwd)/input:/app/input:ro" \
  -v "$(pwd)/output:/app/output" chameleon
```

All dependencies are installed during the build. The renderer Git revision
and Python dependency versions are pinned in `requirements.lock`; fonts and
assets are included. No model, model downloads, inference service or network
connection is required at runtime.

For a stronger check that also verifies the resulting artifacts:

```sh
python scripts/verify_docker.py
```

This builds **linux/amd64**, runs with **no network and a read-only image**,
and validates the three official samples plus a 2480×3508 poster. If your
Docker daemon runs in a separate VM, `--work-dir` must name a directory shared
with that VM. Evidence is written to `artifacts/verification/`.

## How reflow works

1. **Analyze structure.** Infer backgrounds, logos, hero images, text hierarchy,
   grouped/implicit buttons, badges, ribbons, cards, shadow text and footers
   from source geometry, stacking, grouping, typography and asset properties.
   Element ids do not determine roles.
2. **Generate layouts.** Compare vertical, split-landscape, banner-column and
   compact-banner candidates, plus 7 px-minimum variants on small canvases.
   When the target has nearly the source's shape, a proportional candidate
   keeps the designer's composition and shares the spare length out like the
   source's own gaps. Pictures are sized by their visible pixels, not their
   transparent padding, and spare room grows the hero and type (up to about
   the source's density) instead of becoming empty bands. Button, badge and
   ribbon labels never outgrow the headline. Layout decisions depend on
   content and proportions rather than individual sample cases.
3. **Measure type.** Use local brand fonts and Skia glyph metrics with the
   renderer's wrapping routine. Typography follows source scale and hierarchy;
   constrained content rewraps before relaxing readability preferences.
4. **Enforce the contract.** Validate the provisional official JSON schema,
   preserve ids and content/style fields, keep non-cover images in source
   proportions, and contain both declared and rotated bounds.
5. **Audit and choose.** Check collisions (on visible pixels), text overflow,
   readability, grouping, hierarchy, brand fidelity and composition: visible
   hero and logo size, dead bands, and fidelity to the source on near-same
   formats. A candidate that cannot fit is skipped;
   unexpected errors are logged. A contained source fallback keeps a batch
   running if every candidate fails.

The converter corrects end-exclusive `style_runs` indexing and renders large
canvases without the former 2048 px thumbnail cap. It uses cr-renderer's
rendering algorithms; neither the engine nor the studio invents image pixels.

## Verification

```sh
python -m unittest discover -s tests -v
python scripts/verify_submission.py --seeds 60
cd express-addon
npm ci
npm test
npm run build -- --analytics off
```

The Python suite tests reflow, brand/content preservation, deterministic
results, crash fallback, rotations, real PNG sizes, style runs, error handling,
HTTP validation, uploaded assets and campaign exports. The verifier exercises
three samples and 60 seeded generated designs across 20 canvas formats:
**1,260 cases**, reporting structural failures separately from quality issues.
The generated report is the evidence; the displayed local score is not an
Adobe score and does not guarantee performance on hidden designs.

The shipped advanced 728×90 sample now preserves all 17 elements with text at
**7 px or larger** and no local collision/overflow/small-text flags. Seven
pixels remains small in practice. Other copy-heavy micro canvases can still
require smaller text; the studio reports those trade-offs. The engine cannot
promise comfortable reading for arbitrary amounts of content on any size.

GitHub Actions runs Python tests, the resize sweep, the offline container
verification and Adobe's manifest build when the repository is pushed.

## Submission and finale

The attached challenge brief requires **engine + Dockerfile + approach/run
README for stage one**. The full Express add-on, pitch and brief demo video
are finale deliverables. The earlier starter FAQ's stage-one viewer requirement
has been corrected to match that brief.

- [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md): evidence and outstanding acceptance checks.
- [`express-addon/README.md`](express-addon/README.md): build and sideload instructions.
- [`docs/PITCH.md`](docs/PITCH.md): short pitch.
- [`docs/DEMO.md`](docs/DEMO.md): walkthrough and live demo sequence.

The Express panel imports schema designs and inserts the rendered result onto
a new target-size page. It currently inserts a single PNG; native reading and
rewriting of arbitrary existing Express document layers is not implemented.
Its manifest and sandbox behavior are tested locally. Live host acceptance
requires a signed-in Express developer session and trusted local certificate.

Generate the source submission and add-on bundles:

```sh
python scripts/package_submission.py
```

`artifacts/chameleon-submission.zip` explicitly includes new source files even
before a Git commit. Its `submission-files.json` records their SHA-256 hashes.
It also includes available test evidence and the generated demo video.
`artifacts/chameleon-express-addon.zip` places the manifest at its root.
Neither bundle includes virtual environments, private keys or node_modules.

The optional video authoring script needs `imageio-ffmpeg`:

```sh
pip install imageio-ffmpeg
python scripts/make_demo.py
```

The resulting MP4 demonstrates real engine outputs. It does not claim to be a
recording of a live Adobe Express host session.
