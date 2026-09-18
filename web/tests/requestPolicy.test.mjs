import assert from "node:assert/strict";
import test from "node:test";
import { trustedBrowserRequest } from "../.test-build/requestPolicy.js";

const local = { host: "localhost:3000", origin: null, fetchSite: null };

test("local defaults accept loopback but reject DNS rebinding and untrusted origins", () => {
  assert.equal(trustedBrowserRequest(local), true);
  assert.equal(trustedBrowserRequest({ ...local, host: "127.0.0.1:3000", origin: "http://localhost:3000" }), true);
  assert.equal(trustedBrowserRequest({ ...local, host: "[::1]:3000" }), true);
  for (const change of [
    { host: "attacker.example" }, { host: "localhost.attacker.example" }, { host: "" },
    { origin: "null" }, { origin: "https://attacker.example" },
    { origin: "http://localhost:9999" }, { fetchSite: "cross-site" }, { mode: "typo" },
  ]) assert.equal(trustedBrowserRequest({ ...local, ...change }), false);
});

test("cloud requires an explicit exact Host and trusted browser Origin", () => {
  const cloud = { ...local, mode: "cloud", host: "scheduler.example",
    allowedHosts: "scheduler.example,127.0.0.1:8000", allowedOrigins: "https://scheduler.example" };
  assert.equal(trustedBrowserRequest(cloud), true); // API still requires authentication
  assert.equal(trustedBrowserRequest({ ...cloud, origin: "https://scheduler.example" }), true);
  assert.equal(trustedBrowserRequest({ ...cloud, allowedHosts: "" }), false);
  assert.equal(trustedBrowserRequest({ ...cloud, host: "attacker.example" }), false);
  assert.equal(trustedBrowserRequest({ ...cloud, origin: "https://attacker.example" }), false);
});