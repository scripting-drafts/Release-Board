"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { tagActionState } = require("../renderer/tag-actions.js");

const base = {
  name: "v1.2.3",
  remote: "origin",
  present: true,
  signed: true,
  pointsAtHead: true,
  pushed: true,
};

test("missing tag offers Create tag", () => {
  const action = tagActionState(
    { ...base, state: "missing", present: false, signed: false, pointsAtHead: false, pushed: false },
    {}
  );
  assert.equal(action.action, "missing");
  assert.equal(action.label, "Create tag");
  assert.equal(action.disabled, false);
});

test("tag not at HEAD offers Repair tag", () => {
  const action = tagActionState({ ...base, state: "stale", pointsAtHead: false }, {});
  assert.equal(action.action, "stale");
  assert.equal(action.label, "Repair tag");
  assert.equal(action.disabled, false);
});

test("unsigned tag offers Re-sign tag", () => {
  const action = tagActionState({ ...base, state: "unsigned", signed: false }, {});
  assert.equal(action.action, "unsigned");
  assert.equal(action.label, "Re-sign tag");
  assert.equal(action.disabled, false);
});

test("unpushed tag offers Push tag", () => {
  const action = tagActionState({ ...base, state: "unpushed", pushed: false }, {});
  assert.equal(action.action, "unpushed");
  assert.equal(action.label, "Push tag");
  assert.equal(action.disabled, false);
});

test("current tag is disabled", () => {
  const action = tagActionState({ ...base, state: "current" }, {});
  assert.equal(action.action, "current");
  assert.equal(action.label, "Tag current");
  assert.equal(action.disabled, true);
});

test("unknown tag offers Retry on refresh", () => {
  const action = tagActionState(
    { ...base, state: "unknown", pushed: false, message: "cannot inspect origin tags" },
    {}
  );
  assert.equal(action.action, "retry");
  assert.equal(action.label, "Retry on refresh");
  assert.equal(action.disabled, false);
});

test("remote error offers Retry on refresh", () => {
  const action = tagActionState({ ...base, state: "missing" }, { error: "network down" });
  assert.equal(action.action, "retry");
  assert.equal(action.label, "Retry on refresh");
  assert.equal(action.title, "network down");
});

test("loading disables the action", () => {
  const action = tagActionState(null, { loading: true });
  assert.equal(action.action, "checking");
  assert.equal(action.label, "Checking tag…");
  assert.equal(action.disabled, true);
});

test("running task disables direct actions", () => {
  const action = tagActionState(
    { ...base, state: "missing", present: false, signed: false, pointsAtHead: false, pushed: false },
    { busy: true }
  );
  assert.equal(action.action, "missing");
  assert.equal(action.disabled, true);
});

test("unknown state without a recognised action retries", () => {
  const action = tagActionState({ ...base, state: "sideways" }, {});
  assert.equal(action.action, "retry");
  assert.equal(action.label, "Retry on refresh");
});
