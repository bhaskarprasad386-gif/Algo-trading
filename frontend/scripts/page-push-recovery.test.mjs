import test from "node:test";
import assert from "node:assert/strict";
import { isCurrentRecoveryResponse, isCurrentPagePushRequest, mergeUniqueAlerts, freshEarlyEntryAlerts, shouldAllowBoxSpreadEdgeTimingRestRefresh } from "../lib/page-push-recovery.mjs";

test("REST recovery response applies only while its request remains current", () => {
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:8,latestSequence:8,responseValid:true}),true);
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:7,latestSequence:8,responseValid:true}),false);
  assert.equal(isCurrentRecoveryResponse({active:false,requestSequence:8,latestSequence:8,responseValid:true}),false);
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:8,latestSequence:8,responseValid:false}),false);
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:-1,latestSequence:-1,responseValid:true}),false);
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:8,latestSequence:-1,responseValid:true}),false);
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:8,latestSequence:8.5,responseValid:true}),false);
  assert.equal(isCurrentRecoveryResponse({active:true,requestSequence:Number.MAX_SAFE_INTEGER + 1,latestSequence:Number.MAX_SAFE_INTEGER + 1,responseValid:true}),false);
});

test("alert merge replaces duplicate IDs, sorts and bounds rows", () => {
  const current=[{id:1,observed_at_ns:10,state:"EARLY_ENTRY"},{id:2,observed_at_ns:20,state:"WATCH"}];
  const incoming=[{id:1,observed_at_ns:30,state:"INVALIDATED"},{id:3,observed_at_ns:15,state:"EARLY_ENTRY"}];
  const rows=mergeUniqueAlerts(current,incoming,(a,b)=>b.observed_at_ns-a.observed_at_ns,2);
  assert.deepEqual(rows.map(row=>row.id),[1,2]);
  assert.equal(rows[0].state,"INVALIDATED");
  assert.equal(rows[0].observed_at_ns,30);
});

test("fresh early-entry detection respects seen IDs and state", () => {
  const seen=new Set([5]);
  const rows=[{id:5,state:"EARLY_ENTRY"},{id:6,state:"WATCH"},{id:7,state:"EARLY_ENTRY"}];
  assert.deepEqual(freshEarlyEntryAlerts(rows,seen),[rows[2]]);
  assert.deepEqual([...seen],[5]);
});


test("PagePush invalidates in-flight REST recovery across superseding request sequences", () => {
  assert.equal(isCurrentPagePushRequest({active:true,requestSequence:3,latestSequence:3}),true);
  assert.equal(isCurrentPagePushRequest({active:true,requestSequence:2,latestSequence:3}),false);
  assert.equal(isCurrentPagePushRequest({active:false,requestSequence:3,latestSequence:3}),false);
  assert.equal(isCurrentPagePushRequest({active:true,requestSequence:-1,latestSequence:-1}),false);
  assert.equal(isCurrentPagePushRequest({active:true,requestSequence:1.2,latestSequence:1.2}),false);
  assert.equal(isCurrentPagePushRequest({active:true,requestSequence:Number.MAX_SAFE_INTEGER+1,latestSequence:Number.MAX_SAFE_INTEGER+1}),false);
});


test("manual Box Spread edge timing refresh is blocked while PagePush is live", () => {
  assert.equal(shouldAllowBoxSpreadEdgeTimingRestRefresh("LIVE"), false);
  assert.equal(shouldAllowBoxSpreadEdgeTimingRestRefresh("CONNECTING"), true);
  assert.equal(shouldAllowBoxSpreadEdgeTimingRestRefresh("RECONNECTING"), true);
  assert.equal(shouldAllowBoxSpreadEdgeTimingRestRefresh("DISCONNECTED"), true);
});
