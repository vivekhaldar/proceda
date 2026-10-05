# Decision models inside Proceda

Open [`docs/decision-model-experiments.html`](../../../docs/decision-model-experiments.html) directly in a browser. The 24-slide deck is self-contained: charts, flowcharts, styles and scripts work offline without a build step.

- Arrow keys, Page Up/Down or Space: navigate.
- Home/End: first/last slide.
- `O`: overview; `N`: presenter notes; `F`: fullscreen.
- Print: one 16:9 slide per PDF page.
- A URL ending in `#slide-18`, for example, opens the combined cascade.

The deck explains the Qwen/Jev completion experiment, the bounded business-decision experiment, and the cumulative result from its four-arm rerun. It keeps the two experiments' counts separate. In particular, the cumulative 752-call reduction is 609 completion bypasses, 141 direct email decisions and two other email differences; it does not add savings across different runs.

[`data-review.json`](data-review.json) records audited metrics, comparison boundaries and hashes of the source reports and analyses. Presenter notes provide attribution, accounting and accuracy caveats. Costs are protocol estimates, not verified invoices.

The [preview](../../../docs/decision-model-experiments-preview.png) shows an actual completion-check example. Slides 6–7 and 12–13 contain two actual Jev calls per experiment. Their full requests, responses and evidence hashes are in [`completion-examples.json`](completion-examples.json) and [`business-examples.json`](business-examples.json).

The visual style follows [Enchiridion Labs](https://enchiridionlabs.online/): a near-black background, jade and amber accents, Fraunces headings, Crimson Pro body text and JetBrains Mono labels. The three Latin font subsets are embedded in the HTML for offline use, along with their SIL Open Font License notices. Font sources and copies of the licenses are in [`docs/decision-model-font-licenses`](../../../docs/decision-model-font-licenses/).
