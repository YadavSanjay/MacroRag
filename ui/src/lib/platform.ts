// Platform-aware modifier key. The submit handlers accept ⌘ (mac) or Ctrl
// (Windows/Linux); show the label that matches the user's platform rather than
// hardcoding the mac glyph.
const isMac =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || "");

export const modKey = isMac ? "⌘" : "Ctrl";

/** e.g. "⌘↵" on mac, "Ctrl↵" elsewhere — for "Ask  {submitHint}" button labels. */
export const submitHint = `${modKey}↵`;
