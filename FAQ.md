# Chameleon — Project FAQ

This project FAQ aligns submission scope with the supplied **Chameleon
Challenge.pdf**, particularly sections 5.1 and 7.3. The original starter FAQ
had older viewer and renderer instructions that conflicted with that brief.

## What do we submit for stage one?

The engine code, Dockerfile, dependencies and a short approach/run README.
The engine must run offline in its container, consume `input/*.json` tasks,
and emit a JSON layout and real rendered PNG per task. The supplied submission
instructions require the GitHub repository link and registered team details
through the organizer's form, with no separate file upload. The October 22 recap
sets the deadline to October 30, 2026; no cutoff time/zone is stated. Submit
`main`; see `docs/SUBMISSION.md`.

## Is an Adobe Express add-on required for stage one?

The attached brief lists the full Express add-on, short pitch and brief demo
video as **finale** deliverables. This repo includes an add-on panel and the
local studio, but the engine is the automated stage-one scoring entry point.

## What can change during stage-one reflow?

Positions, sizes, geometry and text font size adapt. The same element ids,
content, font families, colours and asset references survive. No elements are
invented, removed, hidden to cheat the evaluator, or replaced by generated
imagery. Cover-fit images may crop using the existing asset. Shape primitives
are rasterized by the normal renderer.

## How do we preview?

Run `python -m engine.main` to create actual rendered outputs, or
`python -m studio.app` for the interactive studio at http://localhost:5050.
No separate transformer-converter application is needed.

## Where are the fonts and images?

`brand_kit/fonts/` and `brand_kit/assets/`. The Level 3 holiday badge is
intentionally missing. Do not fabricate a replacement asset; its placeholder
and warning exercise the expected failure path. The studio can accept custom
image assets matching the `asset_id` filenames in an imported schema design.

## Does a local 100/100 mean we will get full marks?

No. It is a local diagnostic score, separate from Adobe's hidden evaluation.
Visual intent, contrast, composition and unseen designs still matter. Very
tight canvases can fit geometrically while remaining uncomfortable to read.

## What does the Express add-on insert?

A rendered PNG on a new page at the target size. Downloaded layout JSON keeps
the element data. Existing arbitrary Express document layers are not imported
or rewritten by the current panel. See `express-addon/README.md` for sideloading
and the live-host acceptance checklist.

## How do we know every source file made it into the submission?

Run `python scripts/package_submission.py`. It packages an explicit list of
source folders, including files not yet committed, and records SHA-256 hashes.
For Git-based delivery, commit the complete implementation before pushing.
