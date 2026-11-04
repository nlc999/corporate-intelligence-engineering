# Workforce Change Evidence — React / TypeScript / Recharts

One standalone, interactive chart extracted and adapted from a larger corporate-intelligence dashboard. This is a **small frontend engineering evidence sample**, not a replica of the original application.

## Run

Requires Node.js 22+ and npm. From `frontend/`:

```bash
npm install
npm run dev
# open http://127.0.0.1:5173
```

Validate the isolated domain logic and TypeScript/Vite build:

```bash
npm test
npm run build
```

## What to inspect

- [`WorkforceChart.tsx`](src/WorkforceChart.tsx) — React filter state, typed components, one responsive Recharts bar visualisation, lifecycle-separated series, accessible filter controls.
- [`workforceData.ts`](src/workforceData.ts) — pure projection of **individual, exact** signed employment events into chart rows; no artificial totals or continuous trend.
- [`employmentBounds.ts`](src/employmentBounds.ts) — adapted original source logic representing inequalities such as “up to 1,700” as **bounds**, not exact data points.
- [`workforceData.test.ts`](src/workforceData.test.ts) — Node's built-in test runner verifies exact-versus-bounded data, lifecycle filters, ordering, non-finite inputs and immutability.
- [`syntheticData.ts`](src/syntheticData.ts) — fictional examples only.

## Design choices

**Do not conflate lifecycle stages:** announced and planned workforce reductions are not completed changes. They appear in separate, labelled series. This is not a historical company headcount series.

**Do not turn intervals into point observations:** qualified limits such as “up to 1,700” or “more than 1,500” appear only in the text evidence section. The chart excludes them.

**Keep observations independent:** bars are per event rather than totals. No lines suggest continuity between separate announcements or sites. Filtering changes only the view, not the data.

**Keep this demo small:** there is no backend, API key, browser storage, routing, authentication, deployment assumption, live company identity or financial claim.

## Worked UI example (synthetic)

The [bundled invented events](src/syntheticData.ts) illustrate why charting exact values separately matters:

| Event | Presented by the UI | Reason |
| --- | --- | --- |
| Leeds, January 2026: completed reduction of 200 | A signed `-200` bar in the **Completed** series | Exact reported event, not a company-wide net headcount |
| Nationwide, May 2026: announced reduction of *up to 1,700* | `≤ 1,700` in **Uncertain quantities**, not a bar | An upper bound is not an exact realised reduction |
| Bristol, June 2026: completed reduction of *more than 1,500* | `> 1,500` in **Uncertain quantities**, not a bar | Completion is known, but the exact magnitude is not |

To inspect the rendered chart, run `npm run dev` and open the address shown above. No screenshot is bundled; the source and the runnable component are the primary evidence.

## Origin and limitations

Derived as a focused, independently runnable demonstration from the employment chart and bounded-quantity semantics of a private full-stack application. The UI, types and fixtures have been simplified; the underlying concept of excluding uncertain data from exact charts is retained. The real application also handles transfers, unconfirmed changes and workforce increases; this sample intentionally limits visualisation to announced, planned and completed events. **All company-independent event figures are invented.** No claims of observed performance or production deployment are made.
