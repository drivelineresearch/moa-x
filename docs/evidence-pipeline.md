# Evidence-first planning

New CLI, portal and Slack runs use pipeline version 2. Historical artifacts keep
their original schemas and confidence evidence; viewing an archive never fetches
a page or backdates a receipt. Producing model labs remain distinct from the CLI
transport. Broadcast reviewers still receive every surviving proposal.

## Scout and propose

Before Layer 1, a bounded static scout selects relevant repository code,
interfaces and tests using the frozen task, explicit paths, symbols and imports.
It inspects at most 2 MiB of source, at most 64 KiB per file, up to 4,000 eligible
candidate files and 15 focus files. Context caps are 10/16/24 KiB for
Quick/Balanced/Thorough. Partial excerpts identify their captured byte scope.
Symlinks, binaries, environment files, credentials and dependency/build caches
are excluded. Explicit user scope is retained. Repository text remains untrusted
source data. This is deterministic reconnaissance, not a claim that a model has
already understood the repository or executed its tests.

Proposers independently solve the complete task with different emphases such as
minimal change, counterexamples and operational contracts. They cannot see one
another's results. Repository-local questions have no web-search or source-count
quota. External questions still require relevant primary sources. Small plans
and atomic source-bearing premises are preferred to repeated literature surveys.

## Verify and adjudicate

The runner ranks factual checks, presents anonymous proposal labels in a different
order to each reviewer, overlaps essential checks and partitions secondary ones.
Reviewer output maps back to canonical identities, including prose attribution;
its manifest retains the label mapping. Anonymization reduces explicit brand
cues; it does not prove statistical independence or hide every writing-style cue.

| Depth | Checks per reviewer | Shared essential checks | Captured web sources | Source packet cap |
|---|---:|---:|---:|---:|
| Quick | 12 | 6 | 6 | 40,000 characters |
| Balanced | 20 | 10 | 12 | 65,000 characters |
| Thorough | 30 | 15 | 24 | 100,000 characters |

Limits are ceilings, not quotas. A tiny task can finish sooner. Repeated citations
are not independent sources, and neighboring lines never automatically merge
claims. The same primary code file can be sufficient evidence for a code fact;
independent reviewer labs are counted separately from source identity.

Source capture proves which material was available, not whether a claim follows
from it. Code receipts hash safe file/line bytes and flag missing or mismatched
quotes. Web receipts capture bounded text, retrieval time and response hashes;
HTTP connects to DNS-validated public IPs, validates every redirect, has no
cookies/auth/proxy inheritance, and uses a process deadline covering DNS. Failed
or uncaptured pages remain unverified. Captured page text and a model's declared
excerpt are distinguishable. Uploaded reference receipts use
`attachment:<sha256>:<page>`, with filename, extraction/OCR and truncation metadata.
A truncated or inaccessible passage cannot silently become verified. Quotes are
checked against their extracted page. Proposal and synthesis evidence use the
same canonical source identities and quote checks. Multiple passages from one
source remain available to reviewers without counting as independent sources.
Each stage prioritizes its selected sources within a fresh bounded capture budget.

## Final decisions and audit

The v2 companion schema records outcome (`ready`, `needs_revision`, or
`insufficient_evidence`), exact adopted `supporting_claims`, provenance references,
new evidence, counterarguments, constraints, uncertainty and validation. Each
step selects at most three load-bearing proposer claims. Origin references do
not import every assertion from a source step, and a counterargument's citation
is not automatically an adopted premise. A new claim discovered by a reviewer or
the aggregator must carry its own receipt.

The runner puts essential premises and contrary findings before the compact
proposal/review records and deduplicates the research catalog. Exact captured
source windows replace a redundant generic repository packet for synthesis.
The human plan is rendered from the structured decisions, avoiding two separately
authored answers that can disagree.

A separate pass from a successful reviewer in another model lab checks the
actual final decisions against sources, preferring DeepSeek V4.1 Flash. If no
independent successful reviewer is available, readiness remains unverified. Its 180/240/360-second Quick/Balanced/Thorough cap is clipped to the
aggregator phase's remaining deadline. It uses the same bounded recovery machinery,
with one exact JSON schema and one finding per actionable step. Unsupported
receipts become unverified; contradictory findings require revision. The audit
can reduce readiness and confidence but never fabricates a source or inflates
claim-level verification coverage. Unanimous proposal rejection is a supported
result, without a fabricated actionable plan.

## Worker and reader contract

Completed means the process finished. Ready means the structured decision and its
evidence check support proceeding to human review. The portal, Slack and report
expose evidence debt; warning outcomes never imply implementation approval.
Progress shows factual checks and selected-claim coverage. Historical duration
ranges appear only after three comparable completed runs and are not completion
estimates. Raw model confidence remains distinct from the evidence ceiling.

Each native lane has an atomic checkpoint bound to its exact prompt, route,
effort, repository commit and dirty/untracked bytes. An owned worker restart can
resume a v2 job at most twice, reusing matching completed lanes and preserving
terminal failures rather than multiplying provider retries. Changed inputs do
not reuse successful checkpoints. Workspace-mutation failures remain terminal
even when the mutation changed the fingerprint. Aggregation and its final audit
also persist together, so a restart can restore both without another model call. Cancelling jobs are not automatically resumed. Legacy
interrupted runs keep manual recovery. Targeted redispatch excludes sandboxes and
old checkpoints; synthesis-only recovery retains the validated upstream work.

A single POSIX worker lease owns each SQLite queue. Queue exceptions retry with
bounded delay; repeated identical polling errors do not generate an alert every
iteration. `/api/health` checks the real worker thread and queue-error state,
returning HTTP 503 when an expected worker is not ready. Existing auth/access,
billing and workspace-mutation failures remain terminal.

Native usage receipts are retained where a CLI supplies them. Missing usage is
unknown, and provider-reported cost is not assumed to be a subscription charge.
Use [known-answer evaluations](../harness/evals/README.md) before claiming accuracy
gains or changing automatic model weights. One successful depth sweep is an
integration qualification, not a statistically established accuracy improvement.
