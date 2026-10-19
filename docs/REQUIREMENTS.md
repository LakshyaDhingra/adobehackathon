# Challenge requirements and evidence

Scope follows the supplied **Chameleon Challenge.pdf**, sections 2–5 and 7.
This document distinguishes implemented behavior, measured evidence, and
acceptance checks that require the organizer or an Adobe account.

| Requirement | Implementation and evidence | Status |
|---|---|---|
| Standalone automatic reflow, not uniform stretching (§2.1) | `engine.resize.resize`; geometry-derived roles and competing layouts | Implemented and tested |
| Same design structure, ids and content (§2.3, §7.3) | Official JSON schema plus duplicate-id, immutable-field, rotation and aspect checks | Passed local sample/catalogue/generated-design checks |
| Real JSON and PNG artifacts (§7.1–7.2) | Batch CLI, real renderer, exact target PNG dimensions; no silent preview fallback | Passed, including full-size poster |
| Original images only; no regeneration (§7.2) | Assets are read, fitted/cropped and rendered; no generative or replacement-image path | Implemented |
| Fixed font family and copy for stage one (§7.2) | Lossless element copies; changes confined to geometry and text size | Implemented and tested |
| Brand hierarchy, alignment, grouping, no unwanted collisions (§2.5) | Role-aware layout, exact font metrics, group handling and local audit | Good sample results; hidden evaluation remains unknown |
| Readability and nothing cut off (§2.5) | Text fit checks and a final post-containment refit; small-text warnings retained | All three samples have no local quality flags; arbitrary cramped designs remain a quality limit |
| Offline container (§4, §7.1) | Pinned renderer/dependencies and bundled fonts/assets | `linux/amd64` tested with `--network none` and read-only root filesystem |
| Generalization beyond specific examples (§4) | No sample-id branching; seeded generated designs over 20 formats | Reproducible sweep in `scripts/verify_submission.py` |
| Public adapted model weights (§7.2) | No model is used | Not applicable |
| Stage-one engine, Dockerfile, short README (§5.1) | Source files and explicit source-bundle packager | Included |
| Finale Express add-on (§3.3, §5.1) | Manifest-v2 panel, Document SDK sandbox, exact-size new-page PNG insertion | Official CLI build and sandbox tests pass; **live host acceptance pending** |
| Finale short pitch (§5.1) | `docs/PITCH.md` | Included |
| Finale brief demo video (§5.1) | `artifacts/chameleon-demo.mp4`, generated from actual engine outputs | Included; rendered walkthrough, not live Express footage |

## Reproducible evidence

Run these from the repository root after installing dependencies:

```sh
python -m unittest discover -s tests -v
python scripts/verify_submission.py --seeds 60
python scripts/verify_docker.py
cd express-addon
npm ci
npm test
npm run build -- --analytics off
```

Machine-readable reports and logs live under `artifacts/verification/` and
are included in the source bundle when available. Local fit scores are not
Adobe's AI-evaluator scores. Check the report's actual counts and unresolved
quality issues rather than extrapolating from three successful samples.

The reference development sweep uses 63 source designs × 20 formats = 1,260
cases. Structural contract failures are reported independently of small type,
edge spacing and label-fit issues. No content is removed to improve a score.

## Container environment note

The amd64 image was run on an ARM Linux VM using QEMU. That VM's high virtual
address mappings caused PyArrow's allocator to crash before any engine code
ran. Supplying the emulator-only setting
`--qemu-guest-base 0x800000000000` to the verifier resolved it; this is recorded
in `docker-verification.json`. No renderer dependency or rendering algorithm
was altered for that workaround. Native amd64 does not need that flag.
The image also supports a native `linux/arm64` build.

## Outstanding acceptance checks

1. **Express account and certificate:** use the sideload steps in
   `express-addon/README.md`, then verify page insertion, page size, original
   preservation and undo in a signed-in Adobe Express developer session.
   CLI validation and SDK mocks do not establish that the live host accepted it.
2. **Product scope:** the panel consumes Chameleon JSON plus images and inserts
   a single rendered PNG. It does not currently read and rewrite arbitrary
   native Express layers. Downloaded JSON retains the element-level layout.
3. **Organizer logistics:** verify code freeze, submission destination and any
   later hardware/model clarification from the organizers. The supplied brief
   leaves those particulars open; this repository cannot establish them.
4. **Finale video expectation:** if the jury expects Adobe Express footage,
   record the live host acceptance sequence in `docs/DEMO.md` and replace or
   supplement the generated engine walkthrough.

The brief explicitly encourages partial approaches. Good engineering evidence
and submission completeness do not guarantee qualification or perfect layout
quality on every hidden design.
