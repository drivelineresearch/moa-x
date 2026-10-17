# Interface changes

Read this before editing web, Slack, reports, or artwork.

## Web and Slack

Effort copy and controls share one contract: a selected route labeled **Adjust**
reveals an enabled control; other routes say **Fixed _level_ effort** or
**Provider-managed effort**. Use the shared effort presentation logic and keep
route metadata separate from the control container's DOM attributes.

Redact operator email and home-directory details from browser-visible worker
logs. Scanned-PDF OCR stays page-parallel but output-ordered: bound worker ×
Tesseract thread counts, serialize progress updates, and reassemble by page.
Keep private tokens, repository policy, and service configuration outside Git.
See [Web UI](../webui.md) and [Slack](../slack.md) for their full contracts.

## Model identity and reports

Resolve model-lab identity through the shared model-lab module for roster groups,
reviews, live lanes, health portraits, archives, and responsive states. Unknown
custom routes use the independent-lab fallback. Artwork must follow model lab,
including when several labs share an execution harness; see [assets](../assets.md).

The HTML report remains one self-contained file with no external asset requests.
Inline its template, illustrations, and session data. Escape closing-tag
sequences in embedded JSON so captured logs cannot terminate the script element.
See [report](../report.md) and run the applicable [verification](verification.md).
