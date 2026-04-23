/* =============================================================
   Sprawl — shared modal component.

   A minimal "one modal at a time" dialog. Each action (collect,
   buy, list, unlist, withdraw) opens a modal, cycles it through
   a few states (preview → pending → success/error), and closes
   it on dismiss.

   API:
     sprawlModal.open({
       title:   "COLLECT LINK",         // caps label
       body:    [<HTMLElement>...]      // or an HTML string
       buttons: [
         { label: "CANCEL",   kind: "muted",   onClick: close },
         { label: "CONFIRM",  kind: "primary", onClick: async () => { ... } }
       ],
     })

     sprawlModal.setStatus(htmlOrElement)  // replace body
     sprawlModal.setButtons([...])         // replace buttons
     sprawlModal.setTxLink(hash)           // append Etherscan link
     sprawlModal.close()

   The overlay is lazily created once and reused. Press Escape to
   cancel, click the backdrop or [✕] to close. Closing while a tx
   is in flight is allowed — the action keeps running in the
   background; we just stop showing it.
   ============================================================= */
(function () {
  let overlay = null;
  let modal   = null;
  let head    = null;
  let body    = null;
  let foot    = null;
  let txSlot  = null;

  function build() {
    if (overlay) return;
    overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) close();
    });

    modal = document.createElement("div");
    modal.className = "modal";
    overlay.appendChild(modal);

    head = document.createElement("div");
    head.className = "modal-head";
    modal.appendChild(head);

    body = document.createElement("div");
    body.className = "modal-body";
    modal.appendChild(body);

    foot = document.createElement("div");
    foot.className = "modal-foot";
    modal.appendChild(foot);

    document.body.appendChild(overlay);

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && overlay.classList.contains("visible")) close();
    });
  }

  function setTitle(title) {
    head.innerHTML = "";
    const label = document.createElement("span");
    label.textContent = title || "";
    head.appendChild(label);
    const closeBtn = document.createElement("span");
    closeBtn.className = "close";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", close);
    head.appendChild(closeBtn);
  }

  function setBody(content) {
    body.innerHTML = "";
    txSlot = null;
    if (content == null) return;
    if (typeof content === "string") {
      body.innerHTML = content;
    } else if (content instanceof Node) {
      body.appendChild(content);
    } else if (Array.isArray(content)) {
      for (const c of content) {
        if (typeof c === "string") {
          const d = document.createElement("div");
          d.innerHTML = c;
          body.appendChild(d);
        } else if (c instanceof Node) {
          body.appendChild(c);
        }
      }
    }
  }

  function setButtons(buttons) {
    foot.innerHTML = "";
    if (!buttons || !buttons.length) { foot.style.display = "none"; return; }
    foot.style.display = "flex";
    for (const b of buttons) {
      const a = document.createElement("a");
      a.className = "btn btn-" + (b.kind || "muted");
      a.href = "#";
      a.textContent = b.label;
      if (b.disabled) a.classList.add("disabled");
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        if (a.classList.contains("disabled")) return;
        if (typeof b.onClick === "function") b.onClick();
      });
      foot.appendChild(a);
    }
  }

  function setStatus(content) {
    // Replace body content but preserve tx-link slot if it was set.
    setBody(content);
  }

  function setTxLink(hash, explorerBase) {
    if (!hash) return;
    const base = explorerBase || "https://sepolia.etherscan.io/tx/";
    if (!txSlot) {
      txSlot = document.createElement("div");
      txSlot.className = "modal-tx-link";
      body.appendChild(txSlot);
    }
    const short = hash.slice(0, 10) + "…" + hash.slice(-4);
    txSlot.innerHTML = `TX <a href="${base}${hash}" target="_blank" rel="noopener">${short}</a>`;
  }

  function open(opts) {
    build();
    setTitle(opts.title || "");
    setBody(opts.body == null ? "" : opts.body);
    setButtons(opts.buttons || []);
    overlay.classList.add("visible");
  }

  function close() {
    if (overlay) overlay.classList.remove("visible");
  }

  function isOpen() { return overlay && overlay.classList.contains("visible"); }

  // Helper to build a preview row (label | value, side-by-side).
  function row(k, v) {
    const d = document.createElement("div");
    d.className = "modal-row";
    const ks = document.createElement("span"); ks.className = "k"; ks.textContent = k;
    const vs = document.createElement("span"); vs.className = "v"; vs.innerHTML = v;
    d.appendChild(ks); d.appendChild(vs);
    return d;
  }

  // Stacked row for longer prose (link text, entity description, etc.)
  // The value area scrolls vertically if the content exceeds ~160px so
  // the modal doesn't balloon off-screen on long links.
  function rowFull(k, v) {
    const d = document.createElement("div");
    d.className = "modal-row-full";
    const ks = document.createElement("span"); ks.className = "k"; ks.textContent = k;
    const vs = document.createElement("div"); vs.className = "v"; vs.innerHTML = v;
    d.appendChild(ks); d.appendChild(vs);
    return d;
  }

  window.sprawlModal = {
    open, close, isOpen,
    setTitle, setBody, setButtons, setStatus, setTxLink,
    row, rowFull,
  };
})();
