export type PagePushCollectionMutation = {
  collection: string;
  operation: "ADD" | "UPDATE" | "DELETE";
  key_field: string;
  key?: string | number;
  item?: Record<string, unknown>;
};
export function applyCollectionMutations(
  state: Record<string, unknown>,
  mutations: unknown,
): Record<string, unknown>;
