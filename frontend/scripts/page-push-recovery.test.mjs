import test from "node:test";
import assert from "node:assert/strict";
import { isCurrentRecoveryResponse, mergeUniqueAlerts, freshEarlyEntryAlerts, shouldAcceptPageSnapshot, isCurrentPagePushSocket, shouldReloadPairHistory, sendPagePushFrame } from "../lib/page-push-recovery.mjs";

test("REST recovery response applies only while its request remains current", () => {
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:8,latestSequence:8,responseValid:true}),true);
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:7,latestSequence:8,responseValid:true}),false);
  assert.equal(isCurrentRecoveryResponse({active:false,requestSequence:8,latestSequence:8,responseValid:true}),false);
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:8,latestSequence:8,responseValid:false}),false);
});

test("alert merge replaces duplicate IDs, sorts and bounds rows", () => {
  const current=[{id:1,observed_at_ns:10,state:"EARLY_ENTRY"},{id:2,observed_at_ns:20,state:"WATCH"}];
  const incoming=[{id:1,observed_at_ns:30,state:"INVALIDATED"},{id:3,observed_at_ns:15,state:"EARLY_ENTRY"}];
  const rows=mergeUniqueAlerts(current,incoming,(a,b)=>b.observed_at_ns-a.observed_at_ns,2);
  assert.deepEqual(rows.map(row=>row.id),[1,2]);
  assert.equal(rows[0].state,"INVALIDATED");
  assert.equal(rows[0].observed_at_ns,30);
});

test("live alert push preserves loaded older pages after pagination", () => {
  const current = Array.from({length:65},(_,index)=>({id:index+1,observed_at_ns:1000-index,state:"EARLY_ENTRY"}));
  const pushed = [{id:66,observed_at_ns:2000,state:"EARLY_ENTRY"}];
  const rows = mergeUniqueAlerts(current,pushed,(a,b)=>b.observed_at_ns-a.observed_at_ns,Number.POSITIVE_INFINITY);
  assert.equal(rows.length,66);
  assert.equal(rows[0].id,66);
  assert.equal(rows.at(-1).id,65);
});

test("fresh early-entry detection respects seen IDs and state", () => {
  const seen=new Set([5]);
  const rows=[{id:5,state:"EARLY_ENTRY"},{id:6,state:"WATCH"},{id:7,state:"EARLY_ENTRY"}];
  assert.deepEqual(freshEarlyEntryAlerts(rows,seen),[rows[2]]);
  assert.deepEqual([...seen],[5]);
});

import { applyCollectionMutations } from "../lib/page-push-collections.mjs";

const mutation = (operation, key, item) => ({collection:"rows",operation,key_field:"id",key,item});

test("collection ADD is idempotent and collapses duplicate identities", () => {
  const initial={rows:[{id:"x",old:true},{id:"x",duplicate:true},{id:"y"}]};
  const next=applyCollectionMutations(initial,[mutation("ADD","x",{id:"x",value:2})]);
  assert.deepEqual(next.rows,[{id:"x",value:2},{id:"y"}]);
  assert.deepEqual(initial.rows,[{id:"x",old:true},{id:"x",duplicate:true},{id:"y"}]);
});

test("collection UPDATE merges and never resurrects missing rows", () => {
  const state={rows:[{id:1,a:1},{id:2}]};
  assert.deepEqual(applyCollectionMutations(state,[mutation("UPDATE",1,{id:1,b:2})]).rows,[{id:1,a:1,b:2},{id:2}]);
  assert.deepEqual(applyCollectionMutations(state,[mutation("UPDATE",3,{id:3,b:2})]).rows,state.rows);
});

test("DELETE removes all matching identities and subsequent UPDATE cannot recreate", () => {
  const state={rows:[{id:"gone"},{id:"gone"},{id:"stay"}]};
  const deleted=applyCollectionMutations(state,[mutation("DELETE","gone")]);
  assert.deepEqual(deleted.rows,[{id:"stay"}]);
  assert.deepEqual(applyCollectionMutations(deleted,[mutation("UPDATE","gone",{id:"gone",value:1})]).rows,[{id:"stay"}]);
});

test("invalid collection, identity, operation and malformed items are ignored", () => {
  const state={rows:[{id:"a"}]};
  const bad=[
    {collection:"__proto__",operation:"ADD",key_field:"id",key:"b",item:{id:"b"}},
    {collection:"rows",operation:"ADD",key_field:"constructor",key:"b",item:{id:"b"}},
    {collection:"rows",operation:"UPSERT",key_field:"id",key:"b",item:{id:"b"}},
    {collection:"rows",operation:"ADD",key_field:"id",key:"",item:{id:""}},
    {collection:"rows",operation:"ADD",key_field:"id",key:"b",item:{id:"different"}},
    {collection:"rows",operation:"ADD",key_field:"id",key:"b",item:[]},
    {collection:"rows",operation:"ADD",key_field:"id",key:"b",item:{id:true}},
    {collection:"rows",operation:"UPDATE",key_field:"id",key:"b",item:{id:""}},
    {collection:"rows",operation:"ADD",key_field:"id",key:"b",item:{id:null}},
  ];
  assert.deepEqual(applyCollectionMutations(state,bad),state);
});


test("page snapshots cannot rewind an established WebSocket sequence", () => {
  assert.equal(shouldAcceptPageSnapshot({hasSnapshot:false,currentSequence:90,incomingSequence:2}),true);
  assert.equal(shouldAcceptPageSnapshot({hasSnapshot:true,currentSequence:90,incomingSequence:90}),true);
  assert.equal(shouldAcceptPageSnapshot({hasSnapshot:true,currentSequence:90,incomingSequence:91}),true);
  assert.equal(shouldAcceptPageSnapshot({hasSnapshot:true,currentSequence:90,incomingSequence:89}),false);
  assert.equal(shouldAcceptPageSnapshot({hasSnapshot:true,currentSequence:90,incomingSequence:Number.NaN}),false);
});

test("live push invalidates pending runtime-health and diagnostics recovery", () => {
  const requestStartedAtSequence = 12;
  // An authoritative push advances the shared request sequence to 13.
  const latestSequenceAfterPush = 13;
  assert.equal(isCurrentRecoveryResponse({
    active:true, requestSequence:requestStartedAtSequence,
    latestSequence:latestSequenceAfterPush, responseValid:true
  }),false);
  // A replacement recovery request after the push may still apply.
  assert.equal(isCurrentRecoveryResponse({
    active:true, requestSequence:latestSequenceAfterPush,
    latestSequence:latestSequenceAfterPush, responseValid:true
  }),true);
});


test("workspace cleanup invalidates row REST recovery when scanner page changes", () => {
  let sequence = 21;
  const requestStarted = sequence;
  // Cleanup runs on endpoint/slug/custom-mode transition.
  sequence += 1;
  assert.equal(isCurrentRecoveryResponse({
    active:true, requestSequence:requestStarted, latestSequence:sequence, responseValid:true
  }),false);
  // The next scanner recovery request owns the new sequence and may apply.
  const nextRequest = ++sequence;
  assert.equal(isCurrentRecoveryResponse({
    active:true, requestSequence:nextRequest, latestSequence:sequence, responseValid:true
  }),true);
});


test("universe mutation sequencing ignores older response", () => {
  let sequence = 30;
  const first = ++sequence;
  const second = ++sequence;
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:first,latestSequence:sequence,responseValid:true}),false);
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:second,latestSequence:sequence,responseValid:true}),true);
});


test("local trace add or delete invalidates an in-flight initial restore", () => {
  const bootstrapSequence = 0;
  // A local add/delete increments the shared trace recovery sequence.
  const latestSequence = 1;
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:bootstrapSequence,latestSequence,responseValid:true}),false);
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:latestSequence,latestSequence,responseValid:true}),true);
});

import { isPagePushCollectionRemoved } from "../lib/page-push-recovery.mjs";

test("REMOVE frames clear only explicitly removed collections", () => {
  assert.equal(isPagePushCollectionRemoved("REMOVE", ["pair_history"], "pair_history"), true);
  assert.equal(isPagePushCollectionRemoved("UPDATE", ["pair_history"], "pair_history"), false);
  assert.equal(isPagePushCollectionRemoved("REMOVE", ["pairs"], "pair_history"), false);
  assert.equal(isPagePushCollectionRemoved("REMOVE", [], "pair_history"), false);
});


import { trackTraceSave, waitForTraceSave } from "../lib/trace-mutation-order.mjs";

test("trace DELETE waits for an in-flight POST before proceeding", async () => {
  const registry = new Map();
  let resolveSave;
  const save = new Promise(resolve => { resolveSave = resolve; });
  const tracked = trackTraceSave(registry, "trace-1", () => save);
  let deleteStarted = false;
  const deleting = (async () => {
    await waitForTraceSave(registry, "trace-1");
    deleteStarted = true;
  })();
  await Promise.resolve();
  assert.equal(deleteStarted, false);
  resolveSave({ ok: true });
  await Promise.all([tracked, deleting]);
  assert.equal(deleteStarted, true);
  assert.equal(registry.has("trace-1"), false);
});

test("trace DELETE is released when the preceding POST fails", async () => {
  const registry = new Map();
  const tracked = trackTraceSave(registry, "trace-fail", () => Promise.reject(new Error("offline")));
  await assert.rejects(tracked, /offline/);
  await waitForTraceSave(registry, "trace-fail");
  assert.equal(registry.has("trace-fail"), false);
});


test("stale WebSocket callbacks are ignored after transport replacement", () => {
  const oldSocket = {};
  const currentSocket = {};
  assert.equal(isCurrentPagePushSocket(currentSocket, currentSocket), true);
  assert.equal(isCurrentPagePushSocket(currentSocket, oldSocket), false);
  assert.equal(isCurrentPagePushSocket(null, oldSocket), false);
});


test("trace saves for the same ID execute sequentially", async () => {
  const registry = new Map();
  let releaseFirst;
  const firstGate = new Promise(resolve => { releaseFirst = resolve; });
  const order = [];
  const first = trackTraceSave(registry, "same", async () => {
    order.push("first-start");
    await firstGate;
    order.push("first-end");
    return { ok: true };
  });
  const second = trackTraceSave(registry, "same", async () => {
    order.push("second-start");
    return { ok: true };
  });
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(order, ["first-start"]);
  releaseFirst();
  await Promise.all([first, second]);
  assert.deepEqual(order, ["first-start", "first-end", "second-start"]);
  assert.equal(registry.has("same"), false);
});


test("initial and changed history revisions invalidate stale REST history, unchanged revisions do not", () => {
  assert.equal(shouldReloadPairHistory(null, 5), true);
  assert.equal(shouldReloadPairHistory("5", 5), false);
  assert.equal(shouldReloadPairHistory("5", 6), true);
  assert.equal(shouldReloadPairHistory("5", null), false);
});


test("subscribe send exception is handled as a failed control-frame delivery", () => {
  const socket = { send() { throw new Error("socket send race"); } };
  assert.equal(sendPagePushFrame(socket, { action: "subscribe", page: "box-spread" }), false);
});

test("control frames are sent successfully when transport send is healthy", () => {
  let sent = "";
  const socket = { send(value) { sent = value; } };
  assert.equal(sendPagePushFrame(socket, { action: "resync", page: "calendar-spread" }), true);
  assert.equal(sent, JSON.stringify({ action: "resync", page: "calendar-spread" }));
});

test("manual IV runtime status refresh cannot overwrite a newer pushed runtime", () => {
  let latestSequence = 4;
  const requestSequence = ++latestSequence;
  // A page push supersedes the in-flight manual status refresh.
  latestSequence += 1;
  assert.equal(isCurrentRecoveryResponse({
    active:true, requestSequence, latestSequence, responseValid:true
  }),false);
  // A manual refresh started after the push is still allowed to apply.
  const replacementSequence = ++latestSequence;
  assert.equal(isCurrentRecoveryResponse({
    active:true, requestSequence:replacementSequence,
    latestSequence, responseValid:true
  }),true);
});

test("broker snapshot history read is discarded when a newer history push arrives", () => {
  let latestSequence = 30;
  const snapshotHistoryRequest = ++latestSequence;
  // A history revision push invalidates the manual snapshot's delayed REST read.
  latestSequence += 1;
  assert.equal(isCurrentRecoveryResponse({
    active:true, requestSequence:snapshotHistoryRequest,
    latestSequence, responseValid:true
  }),false);
  const recoveryRequest = ++latestSequence;
  assert.equal(isCurrentRecoveryResponse({
    active:true, requestSequence:recoveryRequest,
    latestSequence, responseValid:true
  }),true);
});

test("older-alert pagination response is rejected after a pushed runtime", () => {
  let latestSequence = 9;
  const olderPageRequest = ++latestSequence;
  // Runtime push changes the newest alert set and invalidates pagination.
  latestSequence += 1;
  assert.equal(isCurrentRecoveryResponse({
    active:true, requestSequence:olderPageRequest,
    latestSequence, responseValid:true
  }),false);
  const newOlderPageRequest = ++latestSequence;
  assert.equal(isCurrentRecoveryResponse({
    active:true, requestSequence:newOlderPageRequest,
    latestSequence, responseValid:true
  }),true);
});

test("broker snapshot response is fully ignored after runner configuration supersedes it", () => {
  let latestSequence = 50;
  const snapshotRequest = ++latestSequence;
  // Saving runner configuration invalidates both the delayed snapshot result
  // and its history request; stale POST notices/errors/finalizers must not apply.
  latestSequence += 1;
  assert.equal(isCurrentRecoveryResponse({
    active:true, requestSequence:snapshotRequest,
    latestSequence, responseValid:true
  }),false);
  const freshSnapshot = ++latestSequence;
  assert.equal(isCurrentRecoveryResponse({
    active:true, requestSequence:freshSnapshot,
    latestSequence, responseValid:true
  }),true);
});


import { applyPagePushRemovePayload } from "../lib/page-push-recovery.mjs";

test("REMOVE ignores same-frame stale payload and mutations for deleted collections", () => {
  const previous = { rows: [{ id: "old" }, { id: "keep" }], pairs: [{ id: "oldPair" }], stats: { total: 4 } };
  const payload = {
    remove_keys: ["rows", "pairs"],
    rows: [{ id: "old" }, { id: "resurrect" }],
    pairs: [{ id: "oldPair" }, { id: "resurrectPair" }],
    stats: { total: 5 },
    collection_changes: [
      { collection: "rows", operation: "ADD", key_field: "id", key: "resurrect", item: { id: "resurrect" } },
      { collection: "pairs", operation: "ADD", key_field: "id", key: "resurrectPair", item: { id: "resurrectPair" } },
      { collection: "stats", operation: "UPDATE", key_field: "id", key: "not-a-row", item: { id: "not-a-row" } }
    ]
  };
  const next = applyPagePushRemovePayload(previous, payload);
  assert.equal("rows" in next, false);
  assert.equal("pairs" in next, false);
  assert.deepEqual(next.stats, { total: 5 });
  assert.deepEqual(previous.rows, [{ id: "old" }, { id: "keep" }]);
});

test("REMOVE without stale deleted collection payload still preserves unrelated updates", () => {
  const next = applyPagePushRemovePayload({ opportunities: [{ id: "x" }], status: "LIVE" }, {
    keys: ["opportunities"],
    status: "DEGRADED"
  });
  assert.equal("opportunities" in next, false);
  assert.equal(next.status, "DEGRADED");
});

import { scannerSnapshotState } from "../lib/page-push-recovery.mjs";

test("reconnect scanner snapshot replaces retained collections and clears missing ones", () => {
  const normalized = scannerSnapshotState({ __event: "SNAPSHOT", rows: [{ id: 1 }], stats: { last_observation_at: "2026-11-19T12:00:00Z" } });
  assert.deepEqual(normalized.rows, [{ id: 1 }]);
  assert.deepEqual(normalized.pairs, []);
  assert.deepEqual(normalized.stats, { last_observation_at: "2026-11-19T12:00:00Z" });
  assert.equal(normalized.scanAt, "2026-11-19T12:00:00Z");
  const empty = scannerSnapshotState({ __event: "SNAPSHOT" });
  assert.deepEqual(empty.rows, []);
  assert.deepEqual(empty.pairs, []);
  assert.deepEqual(empty.stats, {});
  assert.equal(empty.scanAt, null);
});

test("scanner snapshot normalization rejects malformed collections and stats", () => {
  const normalized = scannerSnapshotState({ rows: "stale", pairs: null, stats: [] });
  assert.deepEqual(normalized.rows, []);
  assert.deepEqual(normalized.pairs, []);
  assert.deepEqual(normalized.stats, {});
  assert.equal(normalized.scanAt, null);
});

import { workspaceSnapshotMissing } from "../lib/page-push-recovery.mjs";

test("workspace reconnect snapshot identifies absent live and pair collections", () => {
  assert.equal(workspaceSnapshotMissing({ __event: "SNAPSHOT", live: { data: [] }, pairs: { data: [] } }, "live"), false);
  assert.equal(workspaceSnapshotMissing({ __event: "SNAPSHOT", live: { data: [] }, pairs: { data: [] } }, "pairs"), false);
  assert.equal(workspaceSnapshotMissing({ __event: "SNAPSHOT", pairs: { data: [] } }, "live"), true);
  assert.equal(workspaceSnapshotMissing({ __event: "SNAPSHOT", live: { data: [] } }, "pairs"), true);
  assert.equal(workspaceSnapshotMissing({ __event: "PATCH" }, "live"), true);
});

test("workspace reconnect snapshot detects absent diagnostics, universe, and health", () => {
  for (const key of ["diagnostics", "universe", "runtime_health"]) {
    assert.equal(workspaceSnapshotMissing({ __event: "SNAPSHOT" }, key), true);
  }
  assert.equal(workspaceSnapshotMissing({ diagnostics: {}, universe: {}, runtime_health: {} }, "diagnostics"), false);
  assert.equal(workspaceSnapshotMissing({ diagnostics: {}, universe: {}, runtime_health: {} }, "universe"), false);
  assert.equal(workspaceSnapshotMissing({ diagnostics: {}, universe: {}, runtime_health: {} }, "runtime_health"), false);
});

import { ivSkewSnapshotMissing } from "../lib/page-push-recovery.mjs";

test("IV Skew Flip reconnect snapshot resets missing runtime and basket registry", () => {
  assert.equal(ivSkewSnapshotMissing({ __event: "SNAPSHOT" }, "runtime"), true);
  assert.equal(ivSkewSnapshotMissing({ __event: "SNAPSHOT" }, "basket_registry"), true);
  assert.equal(ivSkewSnapshotMissing({ runtime: {}, basket_registry: {} }, "runtime"), false);
  assert.equal(ivSkewSnapshotMissing({ runtime: {}, basket_registry: {} }, "basket_registry"), false);
  assert.equal(ivSkewSnapshotMissing({ runtime: [] }, "runtime"), true);
});



import { syntheticTraceSnapshotMissing } from "../lib/page-push-recovery.mjs";

test("synthetic trace reconnect snapshot clears absent live opportunities", () => {
  assert.equal(syntheticTraceSnapshotMissing({ __event: "SNAPSHOT", live: { data: [] } }, "live"), false);
  assert.equal(syntheticTraceSnapshotMissing({ __event: "SNAPSHOT" }, "live"), true);
  assert.equal(syntheticTraceSnapshotMissing({ __event: "SNAPSHOT", live: { data: "bad" } }, "live"), true);
  assert.equal(syntheticTraceSnapshotMissing({ __event: "PATCH" }, "live"), true);
  assert.equal(syntheticTraceSnapshotMissing({ __event: "SNAPSHOT" }, "other"), false);
});


import { boxShadowSnapshotMissing } from "../lib/page-push-recovery.mjs";
test("Box Spread shadow reconnect snapshot clears absent diagnostics", () => {
  assert.equal(boxShadowSnapshotMissing({ __event: "SNAPSHOT", shadow: {} }), false);
  assert.equal(boxShadowSnapshotMissing({ __event: "SNAPSHOT" }), true);
  assert.equal(boxShadowSnapshotMissing({ __event: "SNAPSHOT", shadow: [] }), true);
});
