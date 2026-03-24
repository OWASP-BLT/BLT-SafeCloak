const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

test("escapeHtml encodes dangerous characters in caller names", () => {
  const uiPath = path.resolve(__dirname, "../public/js/ui.js");
  const videoPath = path.resolve(__dirname, "../public/js/video.js");
  const uiSource = fs.readFileSync(uiPath, "utf8");
  const videoSource = fs.readFileSync(videoPath, "utf8");

  const context = {
    console,
    TextEncoder,
    TextDecoder,
    setTimeout,
    clearTimeout,
    document: {
      addEventListener: () => {},
    },
  };
  context.globalThis = context;

  vm.createContext(context);
  vm.runInContext(uiSource, context);
  vm.runInContext(videoSource, context);

  const escapeHtml = context.escapeHtml;
  const input = `<img src=x onerror="alert('xss')">&`;
  const output = escapeHtml(input);

  assert.equal(output, "&lt;img src=x onerror=&quot;alert(&#39;xss&#39;)&quot;&gt;&amp;");
});
