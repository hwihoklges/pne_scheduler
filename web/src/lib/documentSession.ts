import type { EditResult, Json, Views } from "./api";

const UNDO_LIMIT = 50;
interface HistoryEntry { project: Json; label: string }

export interface DocumentState {
  project: Json | null;
  views: Views | null;
  selected: string;
  busy: boolean;
  error: string;
  notice: string;
  canUndo: boolean;
  canRedo: boolean;
  undoLabel: string;
}

interface Transport {
  views(project: Json, selected?: string): Promise<{ views: Views }>;
  edit(action: string, project: Json, args: Json): Promise<EditResult>;
}

/** Live document + FIFO mutations, independent of React render closures.
 * Opening a different document invalidates its predecessor's queued work.
 * Within a document no edit is dropped; even undo/redo share the same queue.
 * View requests have a separate generation so selection cannot restore stale
 * views or errors after a mutation, another selection, an open, or unmount.
 */
export class DocumentSession {
  private state: DocumentState = {
    project: null, views: null, selected: "", busy: false, error: "", notice: "",
    canUndo: false, canRedo: false, undoLabel: "",
  };
  private back: HistoryEntry[] = [];
  private forward: HistoryEntry[] = [];
  private listeners = new Set<() => void>();
  private tail: Promise<void> = Promise.resolve();
  private generation = 0;
  private viewVersion = 0;
  private pending = 0;

  constructor(private transport: Transport) {}

  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  private update(next: Partial<DocumentState>) {
    this.state = { ...this.state, ...next,
      canUndo: this.back.length > 0, canRedo: this.forward.length > 0,
      undoLabel: this.back.at(-1)?.label ?? "" };
    this.listeners.forEach((listener) => listener());
  }

  setError = (error: string) => this.update({ error });

  invalidate = () => {
    this.generation++;
    this.viewVersion++;
    this.tail = Promise.resolve();
    this.pending = 0;
    this.update({ busy: false });
  };

  private finish(generation: number) {
    if (generation === this.generation) this.update({ busy: --this.pending > 0 });
  }

  private async refresh(project: Json, generation: number) {
    const version = ++this.viewVersion;
    const selected = this.state.selected;
    const current = () => generation === this.generation && version === this.viewVersion;
    try {
      const body = await this.transport.views(project, selected);
      if (current()) this.update({ views: body.views, selected: body.views.selectedModule });
    } catch (error) {
      if (current()) this.setError(error instanceof Error ? error.message : String(error));
    }
  }

  open = async (project: Json) => {
    this.invalidate();
    const generation = this.generation;
    this.back = [];
    this.forward = [];
    this.pending++;
    this.update({ project, views: null, selected: "", busy: true, error: "", notice: "" });
    try { await this.refresh(project, generation); }
    finally { this.finish(generation); }
  };

  private enqueue(operation: (generation: number) => Promise<void>) {
    const generation = this.generation;
    this.pending++;
    this.update({ busy: true });
    const work = this.tail.then(async () => {
      if (generation !== this.generation) return;
      this.setError("");
      try { await operation(generation); }
      catch (error) {
        if (generation === this.generation) {
          this.setError(error instanceof Error ? error.message : String(error));
        }
      } finally { this.finish(generation); }
    });
    this.tail = work;
    return work;
  }

  apply = (action: string, args: Json = {}) => this.enqueue(async (generation) => {
    const project = this.state.project; // read when this edit starts, not when queued
    if (!project) return;
    const result = await this.transport.edit(action, project, args);
    if (generation !== this.generation) return;
    this.back = [...this.back, { project, label: result.label }].slice(-UNDO_LIMIT);
    this.forward = [];
    this.viewVersion++; // invalidate older open/selection views before publishing
    const selected = this.state.selected;
    const needsRefresh = !!selected && selected !== result.views.selectedModule;
    this.update({ project: result.project, views: needsRefresh ? null : result.views, notice: result.label || "",
      selected: selected || result.views.selectedModule });
    // Edit responses already contain fresh views. Only request them again when
    // the server's default selection differs, avoiding an unmount on each key.
    if (needsRefresh) {
      await this.refresh(result.project, generation);
    }
  });

  private travel(direction: "undo" | "redo") {
    return this.enqueue(async (generation) => {
      const entries = direction === "undo" ? this.back : this.forward;
      const entry = entries.at(-1);
      const project = this.state.project;
      if (!entry || !project) return;
      entries.pop();
      if (direction === "undo") this.forward.push({ project, label: entry.label });
      else this.back = [...this.back, { project, label: entry.label }].slice(-UNDO_LIMIT);
      this.update({ project: entry.project, views: null,
        notice: `${direction === "undo" ? "되돌림" : "다시 실행"}: ${entry.label}` });
      await this.refresh(entry.project, generation);
    });
  }

  undo = () => this.travel("undo");
  redo = () => this.travel("redo");

  select = async (selected: string) => {
    this.update({ selected, views: null });
    if (this.state.project) await this.refresh(this.state.project, this.generation);
  };
}