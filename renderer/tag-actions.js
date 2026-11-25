"use strict";

(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.tagActions = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const LABELS = {
    missing: "Create tag",
    stale: "Repair tag",
    unsigned: "Re-sign tag",
    unpushed: "Push tag",
    current: "Tag current",
    unknown: "Retry on refresh",
  };

  function actionTitle(state, data) {
    if (state === "missing") return `Create ${data.name} at HEAD`;
    if (state === "stale") return `Replace ${data.name} at HEAD`;
    if (state === "unsigned") return `Recreate ${data.name} as a signed tag`;
    if (state === "unpushed") return `Push ${data.name} to ${data.remote || "origin"}`;
    return data.name || "Release tag";
  }

  function tagActionState(data, options = {}) {
    const busy = Boolean(options.busy);
    const loading = Boolean(options.loading);
    const error = String(options.error || "");
    if (loading) {
      return {
        action: "checking",
        label: "Checking tag…",
        title: "Checking the local and remote release tag",
        disabled: true,
      };
    }
    if (!data || error || data.state === "unknown") {
      return {
        action: "retry",
        label: LABELS.unknown,
        title: error || data?.message || "Retry the tag status check",
        disabled: busy,
      };
    }
    const state = data.state;
    if (state === "current") {
      return {
        action: "current",
        label: LABELS.current,
        title: `${data.name} is signed, at HEAD and pushed`,
        disabled: true,
      };
    }
    if (!LABELS[state]) {
      return {
        action: "retry",
        label: LABELS.unknown,
        title: data.message || "Retry the tag status check",
        disabled: busy,
      };
    }
    return {
      action: state,
      label: LABELS[state],
      title: actionTitle(state, data),
      disabled: busy,
    };
  }

  return { tagActionState, LABELS };
});
