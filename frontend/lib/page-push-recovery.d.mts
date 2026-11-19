export type RecoveryResponseGuard = {
  active: boolean;
  requestSequence: number;
  latestSequence: number;
  responseValid: boolean;
};
export function isCurrentRecoveryResponse(input: RecoveryResponseGuard): boolean;
export type PagePushSnapshotGuard = {
  hasSnapshot: boolean;
  currentSequence: number;
  incomingSequence: number;
};
export function shouldAcceptPageSnapshot(input: PagePushSnapshotGuard): boolean;
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

export function isCurrentPagePushSocket(currentSocket: WebSocket | null, eventSocket: WebSocket): boolean;

export function shouldReloadPairHistory(currentRevision: string | null, nextRevision: string | number | null | undefined): boolean;

export function isPagePushCollectionRemoved(event: unknown, removedKeys: readonly string[], collection: string): boolean;

/** Send a WebSocket control frame without throwing on transport races. */
export function sendPagePushFrame(socket: WebSocket, frame: Record<string, unknown>): boolean;

export function scannerSnapshotState(snapshot: unknown): { rows: Record<string, unknown>[]; pairs: Record<string, unknown>[]; stats: Record<string, unknown>; scanAt: string | null };

export function workspaceSnapshotMissing(snapshot: unknown, collection: string): boolean;

export function ivSkewSnapshotMissing(snapshot: unknown, key: string): boolean;
