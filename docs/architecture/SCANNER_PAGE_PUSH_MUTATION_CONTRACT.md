# Scanner Page Push Mutation Contract

The scanner page WebSocket is the authoritative live-update channel for scanner read models. REST is limited to initial recovery, bounded fallback while the push channel is not LIVE, and user-initiated writes that are confirmed by the server.

## Event semantics

- `SNAPSHOT`: authoritative page state. Replace the subscribed page's state and establish its sequence baseline.
- `PATCH`: merge object fields. For collection fields, send a complete authoritative list when add/update/delete semantics are ambiguous; row deltas must be explicitly named and keyed.
- For keyed collections, a PATCH may include `collection_changes` entries shaped as `{ collection, operation, key_field, key?, item? }`; `operation` is `ADD`, `UPDATE`, or `DELETE`. ADD/UPDATE carry `item`; DELETE carries a stable `key` (or an item containing the key field). Updates merge the keyed item; adds replace an existing same-key item or append; deletes remove the keyed item. Invalid or unknown collection mutations are ignored.
- `REMOVE`: remove top-level fields using `remove_keys` (preferred) or `keys`; any other fields in the same payload are merged. Do not use this event to imply deleting a list row unless the consumer has a keyed-row contract. For list deletion, send the updated complete list in a PATCH.
- Sequence numbers are strictly increasing per page channel. Clients only accept contiguous PATCH/REMOVE frames after SNAPSHOT; otherwise request resynchronization.
- `SNAPSHOT_REQUIRED` is a transport recovery signal, not a page mutation event.

## Add / modify / delete

1. Add: publish the new record in the complete authoritative list or a documented keyed delta.
2. Modify: publish the new record/version with a stable key and monotonic source timestamp/version.
3. Delete: publish the updated complete list (preferred) or an explicit keyed deletion supported by that component.
4. Reconnect: snapshot must contain the committed current state; deleted rows must not reappear.
5. REST recovery results must be request-sequenced and must not overwrite a newer push.
6. Box index spot tick PATCHes may carry a partial `index_spots` list; the frontend merges those entries by symbol. SNAPSHOT replaces the complete spot list.

## Page boundaries and safety

- Public page channels must not contain account-private ledger/rules, tokens, credentials, or broker execution instructions.
- WebSocket browser actions are subscription/resync only. User-initiated mutations stay on authenticated/validated REST endpoints.
- The page push transport must not create broker subscriptions, alter scanner strategy calculations, or route orders.
- Structural frontend changes (new component/layout code) still require a frontend deployment; runtime data changes are pushed without a full page refresh.
