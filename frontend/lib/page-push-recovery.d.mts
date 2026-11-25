export type RecoveryResponseGuard = {
  active: boolean;
  requestSequence: number;
  latestSequence: number;
  responseValid: boolean;
};
export function isCurrentRecoveryResponse(input: RecoveryResponseGuard): boolean;
export type AlertLike = { id: number; state?: string };
export function mergeUniqueAlerts<T extends AlertLike>(
  current: readonly T[],
  incoming: readonly T[],
  compare: (a: T, b: T) => number,
  limit?: number,
): T[];
export function freshEarlyEntryAlerts<T extends AlertLike>(
  alerts: readonly T[],
  seenIds: ReadonlySet<number>,
): T[];

export type PagePushRequestGuard = {
  active: boolean;
  requestSequence: number;
  latestSequence: number;
};
export function isCurrentPagePushRequest(input: PagePushRequestGuard): boolean;
export function shouldAllowBoxSpreadEdgeTimingRestRefresh(pagePushStatus: string, eventDriven?: boolean): boolean;

/** Read a finite spot price from an index's authoritative IV runtime snapshot. */
export function runtimeSpotPrice(runtime: unknown, underlying: string): number | null;
