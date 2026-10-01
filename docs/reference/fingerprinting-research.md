# Fingerprinting research: original proposals and public prior-art audit

Date: 1 October 2026. Scope: MeteoLane's first-party browser recognition, anonymous
vote deduplication and mail-request throttling. This is research, not a deployed change.

The user accepted a documented public novelty audit as the standard for “not already
in the wild.” The conclusion here is bounded: no exact public implementation of the
specified proposals was found in the audited sources. It is not proof of worldwide
absence, a patentability opinion, or a claim that their building blocks are new.

## Proposal 1: conversion-local causal challenges

Instead of asking only what a browser API returns, intervene while the native API
converts an argument, then check whether the returned result respects that intervention.
The proposed contribution is using challenge-selected combinations of these relations
as fingerprint-integrity evidence. This differs from comparing static signal values,
native function text, descriptors, error stacks or two realms' outputs.

Three concrete improvements use this same mechanism; they are not three independent
inventions:

1. **State-intervention witnesses.** Give `measureText` an object whose
   `Symbol.toPrimitive` changes the disposable context's font and returns challenge text.
   Compare against a local control measured with the new font. Likewise, a WebGL enum
   argument changes the disposable context's viewport before yielding `VIEWPORT`.
   A wrapper that snapshots state before conversion can return a stale answer.
2. **Conversion/abort witnesses.** Count argument conversions. In a separate call,
   throw a unique local object from conversion and check that the identical object
   escapes. This catches duplicate inspection and exception swallowing without
   relying on the engine's error wording or printed stack.
3. **Serialization barriers.** A MIME-type object's conversion sets the disposable
   canvas width to zero. Native `toDataURL` should return `data:,`; a wrapper that
   already cloned or serialized the canvas can return the old image instead.

The [Web IDL](https://webidl.spec.whatwg.org/) and
[canvas](https://html.spec.whatwg.org/multipage/canvas.html) specifications supply the
conversion and operation semantics. Those semantics and conformance testing are prior
art, not inventions here. Probe values should be bounded relation/availability outcomes,
not additional stored font widths, pixel images or a new persistent identifier.

### Evidence of useful incremental coverage

The `causal-probes.cjs` research harness transpiles and invokes the current repository's
actual `tamperedNatives` function. A deliberately faulty `measureText` proxy preserves
ordinary primitive arguments and invalid-receiver behavior, masks function text in both
realms and scrubs forwarding frames from error stacks. The existing integrity function
reports `[]`; the font intervention catches its premature state snapshot.

The final rerun produced:

| Configuration, one Apple-silicon host | Native/control trials | Four constructed faulty wrappers caught | Existing integrity on masked proxy | Font intervention on that proxy |
|---|---|---|---|---|
| Chromium 153.0.8010.12 | 32/32 | 32/32 | `[]` | mismatch |
| Firefox 155.0 | 32/32 | 32/32 | `[]` | mismatch |
| Playwright WebKit 26.6 | 32/32 | 32/32 | `[]` | mismatch |
| Firefox 155.0, RFP enabled | 32/32 | 32/32 | `[]` | mismatch |

These are repeated trials, not independent devices or production false-positive rates.
The four constructed faults are premature font snapshots, duplicate conversion,
swallowed conversion exceptions and premature serialization. Transparent forwarding
passes. Playwright WebKit is not shipping Safari with Apple's privacy protections.
The synthetic experiment does not bypass the full production assessment or its
automation checks, and makes no challenge/verify request.

The `source-probes.cjs` harness also executes unmodified public wrapper factories:

- CanvasBlocker serialization returns a nonempty image after conversion emptied the
  original canvas. This happens with cache both enabled and disabled, in all three
  tested engines. Its input-faking `fillText` factory converts text twice; its WebGL
  parameter factory adds property-key conversion after native number conversion.
- Puppeteer Extra's Chromium WebGL vendor proxy disagrees on equivalent enum arguments,
  but the viewport intervention passes and MeteoLane's current stack check already
  reports it. No incremental coverage against that proxy is demonstrated.

These are isolated source tests, **not installed-extension tests**. CanvasBlocker's
policy checker, extension bridge, RNG and preferences are fixtures, and its WebGL
parameter definitions are an empty fixture. Its default protected canvas part is
readout; the text test deliberately forces input-faking. Puppeteer lifecycle handling
uses a minimal superclass shim and a Playwright adapter.

CanvasBlocker is a legitimate privacy tool. Semantic alteration does not prove abuse.
If prototyped in the application, this evidence should start as a separate,
observe-only semantic-alteration diagnostic—not automatically as an enforced lie.
Adaptive wrappers can preserve these relations, and a scripted client can forge a
consistent transcript. A server nonce does not attest client execution. Neither a pass
nor a failure identifies a unique physical device.

## Proposal 2: fingerprint-reclassification debit leases

Separate two decisions: “this print is no longer a reliable identity” and “the spending
already attributed to this print disappears.” The proposed contribution is an
**action-window-bounded debit lease**, triggered by prior accepted spending, that survives
attacker-influenced commonness reclassification without renewing the identity claim.

The current code marks a print common after three new-key networks or five new keys in
an hour. If `fingerprint_common` is enforced, `assess` changes `high` to `low` and
`device_keys` replaces the shared `f:` key with a network/day `p:` key. Equal numbers of
keys do not imply equally strong constraints.

The `reclassification-probes.py` harness invokes the actual assessment, commonness,
device-key and sign-up-counter functions with synthetic evidence and isolated LocMem
cache. It exhausts a shared sign-up counter, triggers the actual three-network threshold,
then checks a fresh key on a fresh network:

| Policy exercised | Fresh-key request after commonness | Same-key request | With existing sign-up age floor enabled |
|---|---|---|---|
| Commonness observe-only | refused: shared `f:` counter remains | refused | fresh key still refused |
| Commonness enforced | allowed: fresh `p:` counter | refused | fresh key still allowed within new IP's allowance |

This is a conditional state-transition result, not a deployed vulnerability claim.
The checked base settings leave `fingerprint_common` observe-only. No deployment
configuration, application endpoint, real account or signature-verification bypass was
tested. The age floor adds the fresh IP's allowance; it does not reproduce the exhausted
cross-network print allowance.

### Proposed policy

For a rule and its current quota slot:

1. If accepted actions have already debited a high-tier `f:` counter, retain that
   existing debit predicate when the same candidate print becomes common.
2. Check and debit the old predicate alongside the current key/network predicates.
   Its presence does not restore a fingerprint ID or deduplicate coverage votes.
3. Let the predicate expire at the original rule-slot boundary. Do not refresh it or
   create a new global debit lease for an already-common print in the next slot.
4. A print classified common before any accepted spending receives no lease.

The research-only sign-up policy uses a candidate print label supplied by the fixture;
production would need a server-internal candidate hash even when `fingerprintId` is
removed. That label would remain untrusted evidence, not authentication.

Assertions verify that two pre-transition debits leave exactly one further accepted
debit, a subsequent new-key/new-network request is refused, the old constraint does
not renew in the next slot, and an initially-common print receives no lease. All passed.
This is a sequential local model, not an atomic Redis implementation, concurrency proof
or implementation for coverage settlement. Honest collisions remain jointly constrained
until the original slot ends; that bounded usability cost needs a policy decision.
It does not prevent changed forged prints, outsourced work or the creation of new
allowances after legitimate window reset. Novelty confidence is weaker than Proposal 1:
the exact composition was not found, but it builds on familiar counter retention.

## Public novelty audit

The audit distinguishes exact mechanisms from broad antecedents:

| Audited source | Existing mechanism; novelty boundary |
|---|---|
| [Picasso](https://research.google/pubs/picasso-lightweight-device-class-fingerprinting-for-web-clients/) | Challenge-response rendering and proof of work already exist. Random canvas challenges alone are not new. |
| [FP-Scanner](https://www.usenix.org/conference/usenixsecurity18/presentation/vastel) | Fingerprint inconsistencies already expose countermeasures. Generic inconsistency checking is not new. |
| [FP-Inconsistent, v3](https://arxiv.org/html/2406.07647v3) | Spatial and temporal attribute-consistency rules already exist. These are not the specific conversion-local interventions. |
| [FP-STALKER](https://ieeexplore.ieee.org/document/8418634/) | Linking evolving fingerprints is not new. |
| [Cross-browser rendering research](https://www.ndss-symposium.org/wp-content/uploads/2017/09/ndss2017_02B-3_Cao_paper.pdf) | Task selection/masking balances rendering stability and uniqueness. A generic update-stable rendering proposal was rejected. |
| [WebKit reentrancy regression](https://github.com/WebKit/WebKit/commit/040ef6e21ffac03b6d6098e0e12354bdb5469ad1) | `getContext` called from settings getters is already a browser test. Reentrant canvas testing itself is not new. The regression was inspected, not executed. |
| [Stripe's multiaccount guidance](https://stripe.com/en-br/resources/more/how-to-detect-fake-users-and-multiaccount-sign-up-abuse) | Attribute-based quotas and link analysis are known. Proposal 2 does not invent fingerprint-based throttling. |
| [Peakhour's rate-limit guidance](https://www.peakhour.io/learning/fingerprinting/network-fingerprinting-for-rate-limiting/) | Confidence-dependent, multidimensional limits already account for drift and collisions. No exact spending-triggered, nonrenewing reclassification lease was identified on the inspected page. |
| [US9807092B1](https://patents.google.com/patent/US9807092B1/en) | Device classification, counters and fingerprint-bound proof of work are published. They are not claimed as new here. This was a technical source comparison, not a legal claims analysis. |

Source-level snapshots and bounded audit scope:

- [CreepJS](https://github.com/abrahamjuliot/creepjs/tree/10aa6724cd33a1015db1574211890518cd04f0cc):
  canvas, WebGL and lies collectors plus related tests searched. Its null-conversion
  check mutates a function's prototype; its `valueOf` target is a Date method, not
  the proposed argument-state intervention.
- [CanvasBlocker](https://github.com/kkapsner/CanvasBlocker/tree/c813333ba94a435d3c164b17320821531834285a):
  modified canvas factories, settings and detection test inspected. The wrapper faults
  motivated tests; a fault's existence is not evidence of an existing detector for it.
- [Puppeteer Extra](https://github.com/berstend/puppeteer-extra/tree/39248f1f5deeb21b1e7eb6ae07b8ef73f1231ab9):
  WebGL vendor evasion and native-text/stack masking utilities inspected and exercised.
- [Kitsune](https://github.com/datascry/kitsune/tree/f0a22b3600a829f7de9d6abb48ff1aaebfb5a69e):
  rule registry, detection catalog, actual `static/home.js` native-invariant and
  `measureText` checks inspected. Collector, detector and probe-tool source searches
  included ignored static files. The inspected checks use function shape and
  cross-realm comparisons, not the proposed conversion-local interventions.
- WPT canvas `text.yaml` and `the-canvas.yaml` were checked for the targeted conversion
  semantics. This was not a full WPT audit.

Search families included browser fingerprinting with `argument coercion`,
`argument conversion`, `conversion order`, `Symbol.toPrimitive`, `reentrant`,
`measureText`, `toDataURL`, and the concrete intervention descriptions; and browser
fingerprints/quotas with `reclassification`, `counter migration`, `confidence`,
`collision`, `poisoning`, `debit lease` and identity changes. Patent-domain searches
included browser fingerprinting with coercion, reentrancy and argument conversion.
No exact public match to the specified proposals was identified. Some searches returned
mostly irrelevant material; their negative results carry little evidentiary weight.
A policy-hot-swap search surfaced an indexed counter-migration excerpt, but full-page
retrieval returned 404; it was not counted as a fully inspected source and reinforces
the weak novelty claim for generic counter retention.

Concurrent-workload/resource-contention identity tests were rejected as an independent
novel proposal: related resource tests appear in
[Douceur](https://www.microsoft.com/en-us/research/publication/the-sybil-attack/),
[SybilControl](https://arxiv.org/abs/1201.2657),
[GPU timing telemetry](https://arxiv.org/abs/2602.09369), and
[WebGPU co-residency research](https://arxiv.org/abs/2606.26412).

This public audit supports **original, precisely bounded proposals**, not a claim of
an entirely new fingerprinting principle. Indexed searches and selected-source audits
cannot exclude other public implementations that were missed or private deployments.

## Reproduction and handoff

The session's research bundle is `/private/tmp/meteolane-fingerprint-research-9RyX9r`.
It includes the detailed chronological report, the pinned public source clones and:

```sh
node /private/tmp/meteolane-fingerprint-research-9RyX9r/causal-probes.cjs /Users/mbo20/src/norain/frontend/node_modules/playwright
node /private/tmp/meteolane-fingerprint-research-9RyX9r/source-probes.cjs
backend/.venv/bin/python /private/tmp/meteolane-fingerprint-research-9RyX9r/reclassification-probes.py
```

All three assertion-backed harnesses passed their latest runs. The bundle uses this
workspace's installed dependencies and paths; it is a temporary research artifact,
not a portable or CI-integrated test suite. No browser accesses a remote page or the
application endpoints during the experiments, and the Python harness uses no database.
Only this research document is added to the repository; application code, API schemas
and generated clients are unchanged.

The research request is answered under the agreed public-audit standard. Before any
production rollout, remaining engineering work includes real installed-tool tests,
the full assessment comparison, mobile/production Safari and other-device validation,
latency measurements, privacy review, observe-only collection and the project's
14-day/manual-device gate for any eventual lie indicator. Those are deployment gates,
not evidence already established by this research.
