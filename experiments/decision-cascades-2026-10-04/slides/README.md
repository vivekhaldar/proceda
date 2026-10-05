# Decision models inside Proceda

Open [`docs/decision-model-experiments.html`](../../../docs/decision-model-experiments.html) directly in a browser. The 19-slide deck is self-contained: charts, flowcharts, styles and scripts work offline without a build step.

- Arrow keys, Page Up/Down or Space: navigate.
- Home/End: first/last slide.
- `O`: overview; `N`: presenter notes; `F`: fullscreen.
- Print: one 16:9 slide per PDF page.
- A URL ending in `#slide-13`, for example, opens the combined cascade.

The deck explains the Qwen/Jev completion experiment, the bounded business-decision experiment, and the cumulative result from its four-arm rerun. It keeps the two experiments' counts separate. In particular, the cumulative 752-call reduction is 609 completion bypasses, 141 direct email decisions and two other email differences; it does not add savings across different runs.

[`data-review.json`](data-review.json) records audited metrics, comparison boundaries and hashes of the source reports and analyses. Presenter notes provide attribution, accounting and accuracy caveats. Costs are protocol estimates, not verified invoices.

The HTML was visually reviewed across all 19 slides; navigation, overview, notes and deep links were checked in a browser. Print verification produced 19 nonempty 960 × 540 pt pages. The [preview](../../../docs/decision-model-experiments-preview.png) is a screenshot of the combined cascade slide.
