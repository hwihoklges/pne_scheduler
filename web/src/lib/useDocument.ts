"use client";

// React renders snapshots; the session owns the live project and mutation queue.
import { useEffect, useState, useSyncExternalStore } from "react";
import { api, type Json } from "./api";
import { DocumentSession } from "./documentSession";
export type { DocumentState } from "./documentSession";

const STORAGE_KEY = "pne_scheduler.draft.v1";
export function useDocument() {
  const [session] = useState(() => new DocumentSession(api));
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);

  useEffect(() => {
    let project = emptyProject();
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) project = JSON.parse(saved);
    } catch {
      // Storage may be unavailable or contain an invalid recovery entry.
    }
    void session.open(project);
    return session.invalidate;
  }, [session]);

  useEffect(() => {
    if (!state.project) return;
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state.project)); }
    catch { session.setError("Draft recovery could not be saved; download the project before closing."); }
  }, [state.project, session]);

  return {
    ...state, open: session.open, apply: session.apply, undo: session.undo,
    redo: session.redo, select: session.select, setError: session.setError,
  };
}

export function emptyProject(): Json {
  return {
    schema: "pne_scheduler.schproj/v2",
    name: "새 스케줄",
    sch_version: 0x00010003,
    cell_profile: { nominal_capacity_mAh: 80.0, v_min: 2.5, v_max: 4.2 },
    modules: [],
    connections: [],
  };
}
