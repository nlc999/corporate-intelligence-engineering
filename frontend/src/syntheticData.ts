import type { WorkforceEvent } from "./workforceData.ts";

/** Invented events only; no company, client, or registry data. */
export const syntheticEvents: readonly WorkforceEvent[] = [
  { id: "a", date: "2023-02-15", geography: "Leeds", lifecycle: "announced", change: -240, bound: null },
  { id: "b", date: "2023-10-20", geography: "Bristol", lifecycle: "completed", change: -125, bound: null },
  { id: "c", date: "2024-05-10", geography: "Manchester", lifecycle: "planned", change: -180, bound: null },
  { id: "d", date: "2024-11-01", geography: "Leeds", lifecycle: "completed", change: -95, bound: null },
  { id: "e", date: "2025-03-05", geography: "Glasgow", lifecycle: "announced", change: -310, bound: null },
  { id: "f", date: "2025-09-28", geography: "Bristol", lifecycle: "planned", change: -150, bound: null },
  { id: "g", date: "2026-01-31", geography: "Leeds", lifecycle: "completed", change: -200, bound: null },
  { id: "h", date: "2026-04-16", geography: "Manchester", lifecycle: "planned", change: -130, bound: null },
  {
    id: "i", date: "2026-05-05", geography: "Nationwide", lifecycle: "announced",
    change: null,
    bound: { isReduction: true, exactChange: null, count: 1700, direction: "upper_bound", inclusive: true, qualifier: "up to" },
  },
  {
    id: "j", date: "2026-06-12", geography: "Bristol", lifecycle: "completed",
    change: null,
    bound: { isReduction: true, exactChange: null, count: 1500, direction: "lower_bound", inclusive: false, qualifier: "more than" },
  },
];
