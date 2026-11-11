export type SaveStatus = "saved" | "pending" | "saving" | "error" | "blocked";

export interface SaveSnapshot<T> {
  value: T;
  status: SaveStatus;
  localSaved: boolean;
  error: string | null;
}

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
type Options<T> = {
  key: string;
  initial: T;
  storage: StorageLike | null;
  isValue: (value: unknown) => value is T;
  valid: (value: T) => boolean;
  save: (value: T) => Promise<unknown>;
  delay?: number;
  restore?: boolean;
};

// A remounted editor joins the same queue, so an older request cannot finish last.
const writes = new Map<string, Promise<void>>();
function serialWrite(key: string, task: () => Promise<void>): Promise<void> {
  const work = (writes.get(key) ?? Promise.resolve()).then(task);
  const settled = work.then(() => undefined, () => undefined);
  writes.set(key, settled);
  void settled.then(() => { if (writes.get(key) === settled) writes.delete(key); });
  return work;
}

export function codePointLength(value: string): number {
  return Array.from(value).length;
}

export function browserStorage(): StorageLike | null {
  try { return typeof window === "undefined" ? null : window.localStorage; }
  catch { return null; }
}

export class Autosave<T> {
  private options: Options<T>;
  private snapshot: SaveSnapshot<T>;
  private saved: string;
  private enabled = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private flight: Promise<void> | null = null;
  private listeners = new Set<() => void>();

  constructor(options: Options<T>) {
    this.options = options;
    this.saved = JSON.stringify(options.initial);
    let value = options.initial;
    let localSaved = false;
    try {
      const raw = options.restore === false ? null : options.storage?.getItem(options.key);
      if (raw !== null && raw !== undefined) {
        const cached: unknown = JSON.parse(raw);
        if (options.isValue(cached)) { value = cached; localSaved = true; }
      }
    } catch { /* The server copy still works if local storage is unavailable. */ }
    this.snapshot = { value, status: !options.valid(value) ? "blocked" :
      JSON.stringify(value) === this.saved ? "saved" : "pending", localSaved, error: null };
  }

  getSnapshot = (): SaveSnapshot<T> => this.snapshot;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  private publish(change: Partial<SaveSnapshot<T>>) {
    this.snapshot = { ...this.snapshot, ...change };
    this.listeners.forEach((listener) => listener());
  }

  private clearTimer() {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private schedule() {
    this.clearTimer();
    if (!this.enabled || !this.options.valid(this.snapshot.value) ||
        JSON.stringify(this.snapshot.value) === this.saved || this.flight) return;
    this.timer = setTimeout(() => { this.timer = null; void this.perform().catch(() => undefined); },
      this.options.delay ?? 600);
  }

  setEnabled(enabled: boolean) {
    this.enabled = enabled;
    if (enabled && this.snapshot.status !== "error") this.schedule();
    else this.clearTimer();
  }

  update(value: T) {
    if (JSON.stringify(value) === JSON.stringify(this.snapshot.value)) return;
    let localSaved = false;
    try {
      if (this.options.storage) {
        this.options.storage.setItem(this.options.key, JSON.stringify(value));
        localSaved = true;
      }
    } catch { /* Surface the actual storage failure in the save status. */ }
    this.publish({ value, localSaved, error: null, status: !this.options.valid(value) ? "blocked" :
      JSON.stringify(value) === this.saved ? "saved" : this.flight ? "saving" : "pending" });
    this.schedule();
  }

  private perform(): Promise<void> {
    if (this.flight) return this.flight;
    if (!this.enabled || !this.options.valid(this.snapshot.value)) return Promise.resolve();
    const value = this.snapshot.value;
    const encoded = JSON.stringify(value);
    if (encoded === this.saved) return Promise.resolve();
    this.publish({ status: "saving", error: null });
    let succeeded = false;
    let skipped = false;
    const work = serialWrite(this.options.key, async () => {
      if (!this.enabled) { skipped = true; return; }
      await this.options.save(value);
      this.saved = encoded;
      succeeded = true;
      this.publish({ status: !this.options.valid(this.snapshot.value) ? "blocked" :
        JSON.stringify(this.snapshot.value) === this.saved ? "saved" : "pending", error: null });
    }).catch((error: unknown) => {
      this.publish({ status: this.options.valid(this.snapshot.value) ? "error" : "blocked",
        error: error instanceof Error ? error.message : String(error) });
      throw error;
    }).finally(() => {
      this.flight = null;
      if (skipped) this.publish({ status: this.options.valid(this.snapshot.value) ? "pending" : "blocked" });
      // Keep failures available for an explicit retry; do not loop while offline.
      if (succeeded || skipped) this.schedule();
    });
    this.flight = work;
    return work;
  }

  async flush(): Promise<T> {
    this.clearTimer();
    if (this.flight) await this.flight;
    while (this.enabled && JSON.stringify(this.snapshot.value) !== this.saved) {
      if (!this.options.valid(this.snapshot.value)) throw new Error("Shorten the text to the displayed limit before continuing.");
      await this.perform();
      this.clearTimer();
    }
    return this.snapshot.value;
  }

  discardCache() {
    try { this.options.storage?.removeItem(this.options.key); } catch { /* Best effort. */ }
  }

  reset(value: T) {
    this.clearTimer();
    this.saved = JSON.stringify(value);
    this.discardCache();
    this.publish({ value, status: "saved", localSaved: false, error: null });
  }
}
