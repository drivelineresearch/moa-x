# Known-answer evaluation

`fixtures/cache-boundaries` is a small, intentionally incorrect repository. Its
four executable acceptance tests distinguish two seeded bugs from two behaviors
that must remain correct. Run ensembles against a disposable copy, never against
a production repository. Run the same frozen request, source bytes and references
at Quick, Balanced and Thorough depths; record exact model IDs and efforts.

Use this task: identify the cache's equality-boundary and falsy-value defects;
propose the smallest correct change; preserve live and missing-key behavior;
cite the code and acceptance tests; do not edit files, add dependencies or research
the web. The known fix is inclusive expiry (`>=`) and returning every unexpired
stored value without a truthiness guard. The offline evidence tests check these
contracts and the structured pipeline separately.

```bash
python3 harness/scripts/evaluate.py \
  --case harness/evals/cases/cache-boundaries.json \
  --session /path/to/quick/session \
  --session /path/to/balanced/session \
  --session /path/to/thorough/session
```

The summary separates accepted lanes, critical-claim coverage, two-lab review,
contradictions, final-audit support, latency, and provider-reported usage. Missing
usage stays unknown; subscription usage is not inferred as a dollar charge.
Contract word mentions are smoke checks, not an accuracy score. Read each final
decision against the executable fixture before accepting it.

For a useful benchmark, add de-identified known-answer cases for a correct
minority against an incorrect majority, incompatible architectures, unanimous
rejection, irrelevant or fabricated citations, stale local code, uploaded PDF
pages and truncated OCR. Include the strongest single-model baseline, ordinary
MoA-X, and one changed mechanism at a time on held-out tasks. Measure factual
precision, missed requirements, incorrect-minority adoption, citation entailment,
cost/usage and p50/p95 latency. Blind human grading and executable checks take
precedence over model self-confidence or writing-style preference. One run per
depth qualifies the integration; it does not establish a statistical improvement
or justify model-routing weights.

Keep real private tasks, run artifacts, account metadata and evaluation results
outside this public repository. Public fixtures must contain synthetic data only.
