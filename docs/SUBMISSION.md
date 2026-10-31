# Stage one submission

Submit this repository link through the organizer's submission form:

**https://github.com/LakshyaDhingra/adobehackathon**

The submitted branch is **`main`**, also the GitHub default branch. All project
branches have been consolidated into its history. The form asks one teammate to
provide the team name, each teammate's registered name/email, university, major,
repository link, and availability for January 11–13, 2027. No separate file upload
is required by the supplied submission instructions. The October 22 recap gives
the final deadline as October 30, 2026; it does not specify a cutoff time or zone.

## Evaluator entry point

The root README documents local installation and the required offline Docker
command. The engine entry point is `python -m engine.main`; the layout interface
is `engine.resize.resize(source, target_canvas)`.

Each JSON task in `/app/input` produces `<task>_output.json` and a real rendered
`<task>_output.png` in `/app/output`. Assets, fonts and the small NumPy model's
weights are committed and copied into the image; no runtime downloads are needed.
The engine preserves every input element, copy, font family and asset reference.

## Final checks

1. Confirm the latest `main` commit's **Verify Chameleon submission** GitHub
   Actions run passes both jobs. It runs the Python suite, 1,260-case sweep,
   linux/amd64 offline container verification, and Adobe add-on build/tests.
2. Submit the root repository link above with the required registration details.
   Keep the submitted revision unchanged after the organizer's code freeze.
3. Retain the submitted commit SHA and the passing Actions run URL as the record
   of the version delivered. A ZIP is optional and can be downloaded from the
   successful Actions run if useful.

Local diagnostics are not Adobe evaluator scores. Dense small canvases retain
readability limitations even when structural checks pass. The full Express
add-on's live host acceptance remains a finale check; it is not a stage-one
submission blocker. The pitch is in `docs/PITCH.md`, and `scripts/make_demo.py`
generates the optional walkthrough video.
