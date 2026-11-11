/** Synchronous guard for a page action. React's busy state only changes after a render. */
export class OperationGate {
  private active = false;
  private serial = 0;
  private current: number | null = null;

  activate(): void { this.active = true; }
  deactivate(): void { this.active = false; this.current = null; this.serial++; }

  begin(): number | null {
    if (!this.active || this.current !== null) return null;
    this.current = ++this.serial;
    return this.current;
  }

  accepts(token: number): boolean { return this.active && this.current === token; }
  finish(token: number): void { if (this.accepts(token)) this.current = null; }
}
