import { cacheGet, cacheSet, enqueue, getQueue, markRejected, removeQueued } from "./offline";

const TOKEN_KEY = "cellier-session";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : "Une erreur est survenue");
    this.status = status;
    this.detail = detail;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

async function parse(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  return contentType.includes("application/json") ? response.json() : response.text();
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
  cacheKey?: string,
): Promise<T> {
  const headers = new Headers(options.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  try {
    const response = await fetch(path, { ...options, headers });
    const body = await parse(response);
    if (!response.ok) {
      if (response.status === 401 && !path.includes("/auth/login")) {
        dispatchEvent(new CustomEvent("cellier:unauthorized"));
      }
      throw new ApiError(response.status, (body as { detail?: unknown })?.detail ?? body);
    }
    const scopedCacheKey = cacheKey ? `${getToken()?.slice(-12) || "public"}:${cacheKey}` : undefined;
    if (scopedCacheKey) await cacheSet(scopedCacheKey, body);
    return body as T;
  } catch (error) {
    if (cacheKey && (error instanceof TypeError || !navigator.onLine)) {
      const scopedCacheKey = `${getToken()?.slice(-12) || "public"}:${cacheKey}`;
      const cached = await cacheGet<T>(scopedCacheKey);
      if (cached !== undefined) return cached;
    }
    throw error;
  }
}

export async function mutation<T>(
  action: "add" | "withdraw" | "move" | "reference_create" | "reserve" | "taste",
  path: string,
  payload: Record<string, unknown>,
): Promise<{ queued: boolean; data?: T }> {
  const operation_id = String(payload.operation_id || crypto.randomUUID());
  const body = { ...payload, operation_id };
  try {
    const data = await api<T>(path, { method: "POST", body: JSON.stringify(body) });
    return { queued: false, data };
  } catch (error) {
    if (error instanceof TypeError || !navigator.onLine) {
      await enqueue({
        operation_id,
        action,
        payload: body,
        created_at: new Date().toISOString(),
        status: "pending",
      });
      return { queued: true };
    }
    throw error;
  }
}

export async function syncQueue(): Promise<{ applied: number; rejected: number }> {
  const operations = (await getQueue()).filter((item) => item.status === "pending");
  if (!operations.length || !navigator.onLine) return { applied: 0, rejected: 0 };
  const result = await api<{
    applied: number;
    rejected: number;
    results: Array<{ operation_id: string; status: string; detail?: unknown }>;
  }>("/api/sync", {
    method: "POST",
    body: JSON.stringify({
      operations: operations.map(({ operation_id, action, payload, created_at }) => ({
        operation_id, action, payload, created_at,
      })),
    }),
  });
  for (const item of result.results) {
    if (item.status === "applied") await removeQueued(item.operation_id);
    else await markRejected(item.operation_id, item.detail);
  }
  dispatchEvent(new CustomEvent("cellier:queue-changed"));
  return result;
}
