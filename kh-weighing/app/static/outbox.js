// Офлайн-очередь рейсов в IndexedDB.
// Рейс сначала пишется сюда, потом отправляется на сервер. Если связи нет — остаётся
// в очереди и уходит позже. Сервер принимает рейс по id идемпотентно, поэтому
// повторная отправка не создаёт дубль.
window.Outbox = (() => {
  const DB_NAME = "kh-weighing";
  const STORE = "outbox";
  let dbPromise = null;

  function open() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "id" });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return dbPromise;
  }

  async function tx(mode, fn) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const t = db.transaction(STORE, mode);
      const store = t.objectStore(STORE);
      let result;
      Promise.resolve(fn(store)).then((r) => { result = r; });
      t.oncomplete = () => resolve(result);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error);
    });
  }

  const req2promise = (req) => new Promise((res, rej) => { req.onsuccess = () => res(req.result); req.onerror = () => rej(req.error); });

  return {
    put: (item) => tx("readwrite", (s) => s.put(item)),
    remove: (id) => tx("readwrite", (s) => s.delete(id)),
    all: async () => {
      const items = await tx("readonly", (s) => req2promise(s.getAll()));
      return (items || []).sort((a, b) => a.body.weighed_at.localeCompare(b.body.weighed_at));
    },
  };
})();
