import type { InfiniteData } from "@tanstack/react-query";

/** Backend `Page[T]` envelope. `next_cursor` is null/absent on the last page. */
export interface Page<T> {
  items: T[];
  next_cursor?: string | null;
}

export type Cursor = string | undefined;

/** Use as `initialPageParam` for every cursor-based useInfiniteQuery. */
export const initialCursor: Cursor = undefined;

/** Use as `getNextPageParam`; returning undefined stops pagination. */
export function nextCursor<T>(page: Page<T>): Cursor {
  return page.next_cursor ?? undefined;
}

export function flattenPages<T>(data: InfiniteData<Page<T>, unknown> | undefined): T[] {
  if (!data) return [];
  return data.pages.flatMap((p) => p.items);
}

/** Drops undefined / empty-string params so they are not sent as `?q=` to the API. */
export function compactParams<T extends Record<string, unknown>>(params: T): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (typeof v === "string" || typeof v === "number") out[k] = v;
  }
  return out;
}
