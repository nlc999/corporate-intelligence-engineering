import test from "node:test";
import assert from "node:assert/strict";
import { boundedObservations, formatSignedChange, toChartRows } from "./workforceData.ts";
import { syntheticEvents } from "./syntheticData.ts";
import type { WorkforceEvent } from "./workforceData.ts";

test("only exact numerical observations reach the visualisation", () => {
  const rows = toChartRows(syntheticEvents, "all");
  assert.equal(rows.length, 8);
  assert.equal(rows.some((row) => row.id === "i" || row.id === "j"), false);
  assert.equal(rows.some((row) => row.announced === -1700 || row.completed === -1500), false);
});
test("events remain independent: no summing, grouping, or filling absent lifecycle series", () => {
  const rows = toChartRows(syntheticEvents, "all");
  assert.equal(rows.length, new Set(rows.map((row) => row.id)).size);
  assert.deepEqual([rows[0].announced, rows[0].planned, rows[0].completed], [-240, null, null]);
  assert.deepEqual([rows[1].announced, rows[1].planned, rows[1].completed], [null, null, -125]);
});
test("lifecycle filter returns only selected exact observations", () => {
  const planned = toChartRows(syntheticEvents, "planned");
  assert.equal(planned.length, 3);
  assert.ok(planned.every((row) => row.lifecycle === "planned"));
  assert.ok(planned.every((row) => row.announced === null && row.completed === null));
});
test("qualified bounds retain inclusive and exclusive inequality semantics", () => {
  const bounds = boundedObservations(syntheticEvents, "all");
  assert.equal(bounds.length, 2);
  assert.equal(bounds[0].label, "≤ 1,700");
  assert.equal(bounds[1].label, "> 1,500");
  assert.match(bounds[0].context, /Not a realised workforce change/);
  assert.match(bounds[1].context, /Not an exact count/);
});
test("bounds are filtered by lifecycle independently of exact chart values", () => {
  assert.equal(boundedObservations(syntheticEvents, "planned").length, 0);
  assert.equal(boundedObservations(syntheticEvents, "announced").length, 1);
  assert.equal(boundedObservations(syntheticEvents, "completed").length, 1);
});
test("nonfinite changes cannot reach the chart", () => {
  const broken: WorkforceEvent[] = [
    { id: "bad", date: "2026-01-01", geography: "Test", lifecycle: "planned", change: Number.NaN, bound: null },
    { id: "inf", date: "2026-01-02", geography: "Test", lifecycle: "planned", change: Infinity, bound: null },
  ];
  assert.deepEqual(toChartRows(broken, "all"), []);
});
test("inconsistent bound candidate is not presented as measured evidence", () => {
  const invalid: WorkforceEvent[] = [{
    id: "invalid", date: "2026-01-01", geography: "Test", lifecycle: "announced", change: null,
    bound: { isReduction: true, exactChange: -200, count: 300, direction: "upper_bound", inclusive: true, qualifier: null },
  }];
  assert.deepEqual(boundedObservations(invalid, "all"), []);
});
test("source events are not mutated during projection and sorting", () => {
  const events = [...syntheticEvents].reverse();
  const start = events.map((item) => item.id);
  const rows = toChartRows(events, "all");
  assert.deepEqual(events.map((item) => item.id), start);
  assert.equal(rows[0].date, "2023-02-15");
});
test("signed changes distinguish reductions from increases", () => {
  assert.equal(formatSignedChange(-240), "-240");
  assert.equal(formatSignedChange(100), "+100");
});
