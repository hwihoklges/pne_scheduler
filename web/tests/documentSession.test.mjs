import assert from "node:assert/strict";
import test from "node:test";
import { DocumentSession } from "../.test-build/documentSession.js";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const view = (project, selected = "") => ({ views: { ...project, selectedModule: selected } });
const result = (project, label) => ({ project, label, views: view(project).views });
const tick = async () => { await Promise.resolve(); await Promise.resolve(); };

for (const failRefresh of [false, true]) {
  test(`mismatched edit views never reach subscribers; refresh failure=${failRefresh}`, async () => {
    const refresh = deferred();
    const session = new DocumentSession({
      views: async (p, s) => p.n === 1 ? refresh.promise : view(p, s),
      edit: async () => ({ ...result({ n: 1 }, "change"), views: view({ n: 1 }, "first").views }),
    });
    await session.open({ n: 0 });
    await session.select("second");
    const snapshots = [];
    session.subscribe(() => snapshots.push(session.getSnapshot()));
    const pending = session.apply("change");
    await tick();
    assert.equal(session.getSnapshot().project.n, 1);
    assert.equal(session.getSnapshot().views, null);
    if (failRefresh) refresh.reject(new Error("selected refresh failed"));
    else refresh.resolve(view({ n: 1 }, "second"));
    await pending;
    for (const state of snapshots) {
      if (state.views) assert.equal(state.views.selectedModule, state.selected);
    }
    assert.equal(session.getSnapshot().selected, "second");
    assert.equal(session.getSnapshot().busy, false);
    if (failRefresh) {
      assert.equal(session.getSnapshot().views, null);
      assert.match(session.getSnapshot().error, /selected refresh failed/);
    } else assert.equal(session.getSnapshot().views.n, 1);
  });
}

test("two queued edits use the live result; undo and redo preserve both history entries", async () => {
  const first = deferred();
  const calls = [];
  const session = new DocumentSession({
    views: async (p, s) => view(p, s),
    edit: async (action, project) => {
      calls.push({ action, project });
      if (action === "first") await first.promise;
      return result({ ...project, [action]: true }, action);
    },
  });
  await session.open({ name: "start" });
  const a = session.apply("first");
  const b = session.apply("second");
  await tick();
  assert.equal(calls.length, 1);
  assert.equal(session.getSnapshot().busy, true);
  first.resolve();
  await Promise.all([a, b]);
  assert.equal(calls[1].project.first, true);
  assert.deepEqual(session.getSnapshot().project, { name: "start", first: true, second: true });
  assert.equal(session.getSnapshot().busy, false);
  await session.undo();
  assert.deepEqual(session.getSnapshot().project, { name: "start", first: true });
  await session.undo();
  assert.deepEqual(session.getSnapshot().project, { name: "start" });
  await session.redo();
  await session.redo();
  assert.equal(session.getSnapshot().project.second, true);
});

test("undo queues behind an outstanding edit, rather than undoing stale state", async () => {
  const edit = deferred();
  const session = new DocumentSession({ views: async (p) => view(p), edit: () => edit.promise });
  await session.open({ n: 0 });
  const applied = session.apply("change");
  const undone = session.undo();
  edit.resolve(result({ n: 1 }, "change"));
  await Promise.all([applied, undone]);
  assert.equal(session.getSnapshot().project.n, 0);
  await session.redo();
  assert.equal(session.getSnapshot().project.n, 1);
});

test("opening a document invalidates pending and queued old-document edits", async () => {
  const edit = deferred();
  let calls = 0;
  const session = new DocumentSession({ views: async (p) => view(p), edit: () => { calls++; return edit.promise; } });
  await session.open({ n: 0 });
  const old = session.apply("change");
  const queued = session.apply("drop only because its document was replaced");
  await tick();
  await session.open({ n: 10 });
  edit.resolve(result({ n: 1 }, "old"));
  await Promise.all([old, queued]);
  assert.equal(calls, 1);
  assert.equal(session.getSnapshot().project.n, 10);
  assert.equal(session.getSnapshot().views.n, 10);
  assert.equal(session.getSnapshot().canUndo, false);
  assert.equal(session.getSnapshot().busy, false);
});

test("late open and selection responses cannot overwrite newer views or errors", async () => {
  const requests = [];
  const session = new DocumentSession({
    views: () => { const d = deferred(); requests.push(d); return d.promise; },
    edit: async () => { throw new Error("unused"); },
  });
  const old = session.open({ n: 0 });
  const current = session.open({ n: 1 });
  requests[1].resolve(view({ n: 1 }));
  await current;
  requests[0].reject(new Error("stale open failure"));
  await old;
  const a = session.select("a");
  const b = session.select("b");
  requests[3].resolve(view({ n: 1 }, "b"));
  await b;
  requests[2].resolve(view({ n: 0 }, "a"));
  await a;
  assert.equal(session.getSnapshot().views.selectedModule, "b");
  assert.equal(session.getSnapshot().views.n, 1);
  assert.equal(session.getSnapshot().error, "");
});

for (const direction of ["undo", "redo"]) {
  test(`late ${direction} refresh cannot overwrite a newly opened document`, async () => {
    const stale = deferred();
    let delay = false;
    const session = new DocumentSession({
      views: async (p) => delay ? stale.promise : view(p),
      edit: async () => result({ n: 1 }, "change"),
    });
    await session.open({ n: 0 });
    await session.apply("change");
    if (direction === "redo") await session.undo();
    delay = true;
    const travel = session[direction]();
    await tick();
    delay = false;
    await session.open({ n: 99 });
    stale.resolve(view({ n: -1 }));
    await travel;
    assert.equal(session.getSnapshot().project.n, 99);
    assert.equal(session.getSnapshot().views.n, 99);
    assert.equal(session.getSnapshot().canUndo, false);
    assert.equal(session.getSnapshot().canRedo, false);
  });
}

test("a rejected edit does not poison the queue or create a history entry", async () => {
  const session = new DocumentSession({
    views: async (p) => view(p),
    edit: async (action, project) => {
      if (action === "bad") throw new Error("invalid");
      return result({ ...project, good: true }, "good");
    },
  });
  await session.open({ n: 0 });
  await Promise.all([session.apply("bad"), session.apply("good")]);
  assert.equal(session.getSnapshot().project.good, true);
  await session.undo();
  assert.equal(session.getSnapshot().canUndo, false);
});

test("selection during an edit cannot leave the old project's views", async () => {
  const selection = deferred();
  const edit = deferred();
  const session = new DocumentSession({
    views: async (p, s) => p.n === 0 && s === "a" ? selection.promise : view(p, s),
    edit: () => edit.promise,
  });
  await session.open({ n: 0 });
  const selected = session.select("a");
  const applied = session.apply("change");
  edit.resolve(result({ n: 1 }, "change"));
  await applied;
  selection.resolve(view({ n: 0 }, "a"));
  await selected;
  assert.equal(session.getSnapshot().views.n, 1);
  assert.equal(session.getSnapshot().selected, "a");
});

test("unmount invalidates outstanding work and its errors", async () => {
  const edit = deferred();
  const session = new DocumentSession({ views: async (p) => view(p), edit: () => edit.promise });
  await session.open({ n: 0 });
  const pending = session.apply("change");
  await tick();
  session.invalidate();
  edit.reject(new Error("late failure"));
  await pending;
  assert.equal(session.getSnapshot().project.n, 0);
  assert.equal(session.getSnapshot().error, "");
  assert.equal(session.getSnapshot().busy, false);
});