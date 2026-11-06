// Klotse's side of the embedded terminal: xterm.js (MIT) in a Qt web view, joined to the Python pty by a QWebChannel.
// Colours and the font come from the page address (#bg=..&fg=..&sel=..&cur=..&font=..&size=..), already checked by Python.
(function () {
  const q = new URLSearchParams(location.hash.slice(1));
  const color = (name, fallback) => (/^#[0-9a-fA-F]{6}$/.test(q.get(name) || "") ? q.get(name) : fallback);
  const bg = color("bg", "#0f1b1f"), fg = color("fg", "#e6edf0");
  document.body.style.background = bg;
  const term = new Terminal({
    fontFamily: q.get("font") || "Consolas, 'DejaVu Sans Mono', Menlo, monospace",
    fontSize: Math.max(8, Math.min(32, parseInt(q.get("size") || "13", 10) || 13)),
    cursorBlink: true, scrollback: 5000, allowProposedApi: false,
    theme: { background: bg, foreground: fg, cursor: color("cur", fg), selectionBackground: color("sel", "#3d6670") },
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(document.getElementById("t"));
  window.term = term;  // used by tests and by Python (screen text)

  const b64ToBytes = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

  new QWebChannel(qt.webChannelTransport, function (channel) {
    const bridge = channel.objects.bridge;
    const sendSize = () => {
      try { if (fit.proposeDimensions()) fit.fit(); } catch (e) { /* not laid out yet: keep the default size */ }
      bridge.resized(term.cols, term.rows);
    };
    bridge.output.connect((b64) => term.write(b64ToBytes(b64)));
    bridge.notice.connect((text) => term.write("\r\n\x1b[2m" + text + "\x1b[0m\r\n"));
    bridge.pasteText.connect((text) => term.paste(text));
    term.onData((data) => bridge.keys(data));
    term.attachCustomKeyEventHandler((e) => {
      if (e.type !== "keydown" || !e.ctrlKey || !e.shiftKey) return true;
      if (e.code === "KeyC") { if (term.hasSelection()) bridge.copy(term.getSelection()); return false; }
      if (e.code === "KeyV") { bridge.requestPaste(); return false; }
      return true;
    });
    window.addEventListener("resize", sendSize);
    new ResizeObserver(sendSize).observe(document.getElementById("t"));
    sendSize();
    term.focus();
    bridge.ready(term.cols, term.rows);
  });
})();
