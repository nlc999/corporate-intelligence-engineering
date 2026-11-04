/**
 * A bounded workforce reduction is evidence of an inequality, not an exact
 * signed delta. These values must never enter observed employment-change charts.
 */
export interface WorkforceBound {
  count: number;
  direction: 'upper_bound' | 'lower_bound';
  inclusive: boolean | null;
  qualifier: string | null;
}

export interface BoundCandidate {
  isReduction: boolean;
  exactChange: number | null;
  count: number | null;
  direction: string | null | undefined;
  inclusive: boolean | null | undefined;
  qualifier: string | null | undefined;
}

export function asBoundedReduction(candidate: BoundCandidate): WorkforceBound | null {
  if (
    !candidate.isReduction
    || candidate.exactChange !== null
    || candidate.count === null
    || !Number.isFinite(candidate.count)
    || candidate.count <= 0
    || (candidate.direction !== 'upper_bound'
        && candidate.direction !== 'lower_bound')
  ) {
    return null;
  }
  return {
    count: candidate.count,
    direction: candidate.direction,
    inclusive: candidate.inclusive ?? null,
    qualifier: candidate.qualifier ?? null,
  };
}

export function workforceBoundDisplay(bound: WorkforceBound): string {
  const number = new Intl.NumberFormat('en-GB').format(bound.count);
  if (bound.inclusive === null) {
    return `${bound.direction === 'upper_bound' ? 'Upper' : 'Lower'} bound: ${number}`;
  }
  if (bound.direction === 'upper_bound') {
    return `${bound.inclusive ? '≤' : '<'} ${number}`;
  }
  return `${bound.inclusive ? '≥' : '>'} ${number}`;
}


/**
 * A bound is not an exact count. Whether it has been realised is a separate
 * lifecycle fact: an imprecisely measured *completed* reduction is still
 * reported as completed. Do not label it "not realised".
 */
export type WorkforceChangeLifecycle =
  | 'announced'
  | 'planned'
  | 'completed'
  | 'transfer'
  | 'unconfirmed'
  | 'increase';

export function workforceBoundLifecycleLabel(
  lifecycle: WorkforceChangeLifecycle,
): string {
  switch (lifecycle) {
    case 'completed':
    case 'increase':
      return 'Not an exact count';
    case 'announced':
    case 'planned':
      return 'Not an exact count · Not a realised workforce change';
    case 'transfer':
      return 'Not an exact count · Workforce transfer';
    case 'unconfirmed':
      return 'Not an exact count · Whether realised is unconfirmed';
  }
}
