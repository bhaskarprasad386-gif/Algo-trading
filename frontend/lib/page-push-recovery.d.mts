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
