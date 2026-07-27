const DB_NAME = "cellier-local";
const DB_VERSION = 1;
const QUEUE = "operation-queue";
const CACHE = "data-cache";

export type QueuedOperation = {
  operation_id: string;
  action: "add" | "withdraw" | "move" | "reference_create" | "reserve" | "taste";
  payload: Record<string, unknown>;
  created_at: string;
  status: "pending" | "requires_review";
  error?: unknown;
};

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(QUEUE)) {
        const store = db.createObjectStore(QUEUE, { keyPath: "operation_id" });
        store.createIndex("created_at", "created_at");
        store.createIndex("status", "status");
      }
      if (!db.objectStoreNames.contains(CACHE)) {
        db.createObjectStore(CACHE, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function transaction<T>(
  storeName: string,
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, mode);
    const request = work(tx.objectStore(storeName));
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    tx.oncomplete = () => db.close();
  }));
}

export async function enqueue(operation: QueuedOperation): Promise<void> {
  await transaction(QUEUE, "readwrite", (store) => store.put(operation));
  dispatchEvent(new CustomEvent("cellier:queue-changed"));
}

export async function getQueue(): Promise<QueuedOperation[]> {
  const rows = await transaction(QUEUE, "readonly", (store) => store.getAll());
  return rows.sort((a, b) => a.created_at.localeCompare(b.created_at));
}

export async function removeQueued(id: string): Promise<void> {
  await transaction(QUEUE, "readwrite", (store) => store.delete(id));
}

export async function markRejected(id: string, error: unknown): Promise<void> {
  const operations = await getQueue();
  const item = operations.find((value) => value.operation_id === id);
  if (item) await enqueue({ ...item, status: "requires_review", error });
}

export async function cacheSet(key: string, value: unknown): Promise<void> {
  await transaction(CACHE, "readwrite", (store) =>
    store.put({ key, value, saved_at: new Date().toISOString() }),
  );
}

export async function cacheGet<T>(key: string): Promise<T | undefined> {
  const row = await transaction<{ key: string; value: T } | undefined>(
    CACHE,
    "readonly",
    (store) => store.get(key),
  );
  return row?.value;
}

export async function clearLocalData(): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction([QUEUE, CACHE], "readwrite");
    tx.objectStore(QUEUE).clear();
    tx.objectStore(CACHE).clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
  if ("caches" in window) {
    const names = await caches.keys();
    await Promise.all(names.filter((name) => name.startsWith("cellier-api")).map((name) => caches.delete(name)));
  }
}
