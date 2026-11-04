/** Small adaptation of the existing lifecycle-aware workforce chart projection.
 * Event observations are independent evidence, NOT a continuous time series,
 * and bounds are never silently coerced to precise numeric changes.
 */
import {
  asBoundedReduction,
  workforceBoundDisplay,
  workforceBoundLifecycleLabel,
  type BoundCandidate,
  type WorkforceChangeLifecycle,
} from "./employmentBounds.ts";

export type Lifecycle = Extract<WorkforceChangeLifecycle, "announced" | "planned" | "completed">;
export type LifecycleFilter = Lifecycle | "all";

export interface WorkforceEvent {
  id: string;
  date: string; // ISO yyyy-mm-dd
  geography: string;
  lifecycle: Lifecycle;
  change: number | null; // Signed precise workforce change, if evidenced
  bound: BoundCandidate | null; // An inequality; deliberately excluded from charts
}

export interface ChartEvent {
  id: string;
  label: string;
  date: string;
  geography: string;
  lifecycle: Lifecycle;
  announced: number | null;
  planned: number | null;
  completed: number | null;
}

export function toChartRows(events: readonly WorkforceEvent[], filter: LifecycleFilter): ChartEvent[] {
  return events
    .filter((event) =>
      (filter === "all" || event.lifecycle === filter)
      && event.change !== null
      && Number.isFinite(event.change)
    )
    .slice()
    .sort((a, b) => a.date.localeCompare(b.date) || a.id.localeCompare(b.id))
    .map((event) => ({
      id: event.id,
      label: event.date.slice(0, 7) + " · " + event.geography,
      date: event.date,
      geography: event.geography,
      lifecycle: event.lifecycle,
      announced: event.lifecycle === "announced" ? event.change : null,
      planned: event.lifecycle === "planned" ? event.change : null,
      completed: event.lifecycle === "completed" ? event.change : null,
    }));
}

export function boundedObservations(events: readonly WorkforceEvent[], filter: LifecycleFilter) {
  return events.flatMap((event) => {
    if ((filter !== "all" && event.lifecycle !== filter)
      || event.change !== null || event.bound === null) return [];
    const parsed = asBoundedReduction(event.bound);
    if (!parsed) return [];
    return [{
      id: event.id,
      date: event.date,
      geography: event.geography,
      label: workforceBoundDisplay(parsed),
      context: workforceBoundLifecycleLabel(event.lifecycle),
    }];
  });
}

export function formatSignedChange(change: number): string {
  return new Intl.NumberFormat("en-GB", { signDisplay: "always" }).format(change);
}
