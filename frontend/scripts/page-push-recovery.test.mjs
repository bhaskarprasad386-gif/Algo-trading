import test from "node:test";
import assert from "node:assert/strict";
import { isCurrentRecoveryResponse, mergeUniqueAlerts, freshEarlyEntryAlerts } from "../lib/page-push-recovery.mjs";

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
  ];
  assert.deepEqual(applyCollectionMutations(state,bad),state);
});
