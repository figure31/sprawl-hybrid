/* =============================================================
   Sprawl — high-level action flows.

   Each function (collect / buy / list / unlist / withdraw) does:
     1. Ensure wallet is connected and on Sepolia (prompts otherwise)
     2. Open a preview modal with the relevant details
     3. On confirm: call the contract, transition the modal through
        "check wallet → submitted → confirming → success/error",
        surface an Etherscan tx link once broadcast
     4. On success dispatch a `sprawl:<action>` CustomEvent on the
        window so pages can update their UI optimistically

   Kind strings ("link"/"entity"/"arc") map to the contract's
   AssetKind enum (0/1/2). bytes32 ids are built per-kind:
     - link   → uint256 linkId padded to 32 bytes
     - entity → keccak256(utf8(entityId))
     - arc    → keccak256(utf8(arcId))

   Dependencies (loaded globally before this script):
     - ethers v6 (UMD build)
     - sprawlWallet (sprawl-wallet.js)
     - sprawlModal  (sprawl-modal.js)
     - SprawlAPI    (sprawl-data.js)
   ============================================================= */
(function () {
  const KIND_ENUM = { link: 0, entity: 1, arc: 2 };
  const KIND_LABEL = { link: "LINK", entity: "ENTITY", arc: "ARC" };

  function kindNumber(kind) {
    const k = String(kind || "").toLowerCase();
    if (!(k in KIND_ENUM)) throw new Error("unknown kind: " + kind);
    return KIND_ENUM[k];
  }

  function assetIdBytes32(kind, nativeId) {
    const k = String(kind || "").toLowerCase();
    if (k === "link") {
      return ethers.zeroPadValue(ethers.toBeHex(BigInt(nativeId)), 32);
    }
    return ethers.keccak256(ethers.toUtf8Bytes(String(nativeId)));
  }

  // Bundle authorSig/operatorSig arrive as 65-byte packed hex:
  // `0x{r:32}{s:32}{v:1}`. The contract's Sig struct is
  // {bytes32 r, bytes32 s, uint8 v} — we pass a positional tuple.
  function unpackSig(packedHex) {
    const h = String(packedHex || "").replace(/^0x/, "");
    if (h.length !== 130) throw new Error("invalid sig length: " + h.length);
    return [
      "0x" + h.slice(0, 64),
      "0x" + h.slice(64, 128),
      parseInt(h.slice(128, 130), 16),
    ];
  }

  function formatEth(weiInput) {
    let w;
    try { w = typeof weiInput === "bigint" ? weiInput : BigInt(weiInput || 0); }
    catch { return "— ETH"; }
    if (w === 0n) return "0 ETH";
    const full = ethers.formatEther(w);
    const [whole, frac] = full.split(".");
    if (!frac) return whole + " ETH";
    const trimmed = frac.replace(/0+$/, "");
    return (trimmed ? whole + "." + trimmed : whole) + " ETH";
  }

  function shortAddr(a) {
    if (!a) return "—";
    return a.slice(0, 6) + "…" + a.slice(-4);
  }

  function idLabel(kind, id) {
    const k = String(kind || "").toLowerCase();
    if (k === "link")   return "#" + id;
    if (k === "entity") return "[" + id + "]";
    if (k === "arc")    return "{" + id + "}";
    return String(id);
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, c => (
      {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]
    ));
  }

  // Wallet errors come in lots of shapes. Prefer a short, human-readable
  // surface; fall back to a truncated .message.
  function formatErr(e) {
    if (!e) return "unknown error";
    // User cancellations shouldn't look like failures.
    if (e.code === 4001 || e.code === "ACTION_REJECTED") return "Request cancelled.";
    if (e.shortMessage) return e.shortMessage;
    if (e.reason)       return e.reason;
    const m = e.message || String(e);
    return m.length > 200 ? m.slice(0, 200) + "…" : m;
  }

  // Caller-supplied authorName may be either a registered Sprawl name or
  // a raw hex address. Registered names are uppercased; raw hex is
  // shortened (0x1234…abcd) and keeps its natural case, matching the
  // site-wide convention used elsewhere on the profile pages.
  function formatAuthor(name) {
    const s = String(name || "");
    if (/^0x[0-9a-fA-F]{40}$/.test(s)) return shortAddr(s);
    return s.toUpperCase();
  }

  function showError(msg) {
    sprawlModal.open({
      title: "ERROR",
      body: `<div class="modal-text">${escapeHtml(msg)}</div>`,
      buttons: [{ label: "CLOSE", kind: "muted", onClick: () => sprawlModal.close() }],
    });
  }

  // Gate every action behind: ethers loaded + wallet available + connected
  // + on Sepolia. Returns null on success, string on soft-failure (we
  // surface it in a modal). Throws are caught by callers.
  async function ensureReady() {
    if (typeof ethers === "undefined") return "Web3 library failed to load.";
    if (!window.ethereum) return "No wallet detected. Install MetaMask, Rabby, or another browser wallet.";
    if (!sprawlWallet.isConnected()) {
      try { await sprawlWallet.connect(); }
      catch (e) { return formatErr(e); }
    }
    if (!sprawlWallet.isOnSepolia()) {
      try { await sprawlWallet.switchToSepolia(); }
      catch (e) { return "Please switch your wallet to the Sepolia network."; }
    }
    return null;
  }

  // Shared modal lifecycle: show preview → confirm → wallet sig →
  // submitted → success/error. txFn returns an ethers tx response.
  // onSuccess fires after receipt; callers use it to dispatch events.
  async function runTxFlow({ title, previewRows, confirmLabel, txFn, onSuccess, successText }) {
    const body = document.createElement("div");
    for (const r of previewRows) body.appendChild(r);

    sprawlModal.open({
      title,
      body,
      buttons: [
        { label: "CANCEL", kind: "muted", onClick: () => sprawlModal.close() },
        { label: confirmLabel, kind: "primary", onClick: () => runConfirmed(txFn, onSuccess, successText) },
      ],
    });
  }

  async function runConfirmed(txFn, onSuccess, successText) {
    sprawlModal.setBody(`<div class="modal-text muted">Check your wallet to confirm the transaction.</div>`);
    sprawlModal.setButtons([]);

    let tx;
    try {
      tx = await txFn();
    } catch (e) {
      sprawlModal.setBody(`<div class="modal-text">${escapeHtml(formatErr(e))}</div>`);
      sprawlModal.setButtons([{ label: "CLOSE", kind: "muted", onClick: () => sprawlModal.close() }]);
      return;
    }

    sprawlModal.setBody(`<div class="modal-text muted">Transaction submitted — waiting for confirmation.</div>`);
    sprawlModal.setTxLink(tx.hash);

    let receipt;
    try {
      receipt = await tx.wait();
    } catch (e) {
      sprawlModal.setBody(`<div class="modal-text">Transaction failed.</div><div class="modal-text muted">${escapeHtml(formatErr(e))}</div>`);
      sprawlModal.setTxLink(tx.hash);
      sprawlModal.setButtons([{ label: "CLOSE", kind: "muted", onClick: () => sprawlModal.close() }]);
      return;
    }

    sprawlModal.setBody(`<div class="modal-text">${escapeHtml(successText || "Confirmed.")}</div><div class="modal-text muted">Subgraph may take a few seconds to catch up.</div>`);
    sprawlModal.setTxLink(receipt.hash || tx.hash);
    sprawlModal.setButtons([{ label: "CLOSE", kind: "primary", onClick: () => sprawlModal.close() }]);

    if (typeof onSuccess === "function") {
      try { onSuccess(receipt); } catch {}
    }
  }

  // --- COLLECT ---
  // Opens with a "Loading details…" state, fetches the bundle + the
  // current firstSalePrice + per-kind mentions in parallel, then renders
  // the same rich preview we use for BUY (KIND / ID / CONTENT / CREATOR
  // / MENTIONS / PRICE) before the CONFIRM step. Content and creator
  // come straight from the bundle — no second call needed.
  async function collect(kind, nativeId, display) {
    const err = await ensureReady();
    if (err) return showError(err);

    const k = String(kind).toLowerCase();

    sprawlModal.open({
      title: "COLLECT",
      body: `<div class="modal-text muted">Loading details…</div>`,
      buttons: [
        { label: "CANCEL", kind: "muted", onClick: () => sprawlModal.close() },
      ],
    });

    let bundle, price, mentionsResp;
    try {
      const read = sprawlWallet.getReadContract();
      const mentionsFetch =
        k === "entity" ? SprawlAPI.entityMentions(nativeId, 50).catch(() => null) :
        k === "arc"    ? SprawlAPI.arcReferences(nativeId, 50).catch(() => null)  :
                         Promise.resolve(null);
      [bundle, price, mentionsResp] = await Promise.all([
        SprawlAPI.collectPrepare(kind, nativeId),
        read.firstSalePrice(),
        mentionsFetch,
      ]);
    } catch (e) {
      sprawlModal.setBody(`<div class="modal-text">Could not load details: ${escapeHtml(formatErr(e))}</div>`);
      sprawlModal.setButtons([{ label: "CLOSE", kind: "muted", onClick: () => sprawlModal.close() }]);
      return;
    }
    if (!bundle) {
      sprawlModal.setBody(`<div class="modal-text">Asset not found, or could not be prepared for collection.</div>`);
      sprawlModal.setButtons([{ label: "CLOSE", kind: "muted", onClick: () => sprawlModal.close() }]);
      return;
    }

    // Derive content + mentions from whatever fits each kind.
    let content = "";
    let mentions = [];
    if (k === "link") {
      content = bundle.text || "";
      mentions = extractTagsFromText(content);
    } else if (k === "entity") {
      content = bundle.description || "";
      if (mentionsResp && Array.isArray(mentionsResp.items)) {
        mentions = mentionsResp.items.map(m => "#" + m.linkId);
      }
    } else {
      content = bundle.description || "";
      if (mentionsResp && Array.isArray(mentionsResp.items)) {
        mentions = mentionsResp.items.map(r => "#" + r.linkId);
      }
    }

    const authorName = (display && display.authorName) || bundle.author;
    const rows = [];
    rows.push(sprawlModal.row("KIND", KIND_LABEL[k]));
    rows.push(sprawlModal.row("ID",   escapeHtml(idLabel(k, nativeId))));
    if (content) rows.push(sprawlModal.rowFull("CONTENT", escapeHtml(truncate(content, 600))));
    rows.push(sprawlModal.row("CREATOR", escapeHtml(formatAuthor(authorName))));
    if (mentions.length) {
      const CAP = 10;
      const shown = mentions.slice(0, CAP).join(", ");
      const more  = mentions.length > CAP ? ` (+${mentions.length - CAP} more)` : "";
      const row   = sprawlModal.row("MENTIONS", escapeHtml(shown + more));
      row.classList.add("oneline");
      rows.push(row);
    }
    const priceRow = sprawlModal.row("PRICE", formatEth(price));
    priceRow.classList.add("spaced-above");
    rows.push(priceRow);

    const body = document.createElement("div");
    for (const r of rows) body.appendChild(r);
    sprawlModal.setBody(body);
    sprawlModal.setButtons([
      { label: "CANCEL",  kind: "muted",   onClick: () => sprawlModal.close() },
      { label: "CONFIRM", kind: "primary", onClick: () => runConfirmed(
        async () => {
          const contract = await sprawlWallet.getContract();
          const authorSig   = unpackSig(bundle.authorSig);
          const operatorSig = unpackSig(bundle.operatorSig);

          if (k === "link") {
            return await contract.collectLink(
              BigInt(bundle.linkId),
              BigInt(bundle.parentId),
              bundle.authoredAt,
              bundle.nonce,
              bundle.beaconBlock,
              bundle.isRecap,
              BigInt(bundle.coversFromId || "0"),
              BigInt(bundle.coversToId || "0"),
              bundle.author,
              ethers.toUtf8Bytes(bundle.text || ""),
              authorSig,
              operatorSig,
              { value: price }
            );
          }
          if (k === "entity") {
            return await contract.collectEntity(
              bundle.entityId,
              bundle.name,
              bundle.entityType,
              bundle.description || "",
              bundle.authoredAt,
              bundle.nonce,
              bundle.beaconBlock,
              bundle.author,
              authorSig,
              operatorSig,
              { value: price }
            );
          }
          // arc
          return await contract.collectArc(
            bundle.arcId,
            BigInt(bundle.anchorLinkId || "0"),
            bundle.description || "",
            bundle.authoredAt,
            bundle.nonce,
            bundle.beaconBlock,
            bundle.author,
            authorSig,
            operatorSig,
            { value: price }
          );
        },
        () => {
          window.dispatchEvent(new CustomEvent("sprawl:collected", {
            detail: { kind: k, nativeId: String(nativeId), owner: sprawlWallet.getAddress() }
          }));
        },
        "Collected."
      )},
    ]);
  }

  // --- BUY ---
  // Opens with a "Loading details…" state, fetches the asset's content
  // + mentions from SprawlAPI in parallel, then renders a full preview
  // before the CONFIRM step. Creator and owner are normally passed by
  // the caller (the marketplace listing already knows both); if they
  // aren't, the modal just omits those rows.
  async function buy(kind, nativeId, priceWei, display) {
    const err = await ensureReady();
    if (err) return showError(err);

    const k = String(kind).toLowerCase();
    const priceBig = BigInt(priceWei || 0);

    // Open modal immediately with a loading state so the click feels
    // responsive; fetches happen behind the scenes.
    sprawlModal.open({
      title: "BUY",
      body: `<div class="modal-text muted">Loading details…</div>`,
      buttons: [
        { label: "CANCEL", kind: "muted", onClick: () => sprawlModal.close() },
      ],
    });

    let details;
    try { details = await fetchBuyDetails(k, nativeId, display); }
    catch (e) {
      sprawlModal.setBody(`<div class="modal-text">Could not load details: ${escapeHtml(formatErr(e))}</div>`);
      sprawlModal.setButtons([{ label: "CLOSE", kind: "muted", onClick: () => sprawlModal.close() }]);
      return;
    }

    const body = document.createElement("div");
    for (const r of buildBuyRows(k, nativeId, priceBig, details)) body.appendChild(r);
    sprawlModal.setBody(body);
    sprawlModal.setButtons([
      { label: "CANCEL",  kind: "muted",   onClick: () => sprawlModal.close() },
      { label: "CONFIRM", kind: "primary", onClick: () => runConfirmed(
        async () => {
          const contract = await sprawlWallet.getContract();
          const id = assetIdBytes32(k, nativeId);
          return await contract.buy(kindNumber(k), id, priceBig, { value: priceBig });
        },
        () => {
          window.dispatchEvent(new CustomEvent("sprawl:bought", {
            detail: { kind: k, nativeId: String(nativeId), owner: sprawlWallet.getAddress() }
          }));
        },
        "Bought."
      )},
    ]);
  }

  // Truncate content for preview. Modal keeps a scroll fallback in CSS,
  // but limiting here keeps the initial footprint small and the buyer's
  // decision quick. Full text remains available via the asset's page.
  function truncate(s, n) {
    const t = String(s || "");
    return t.length > n ? t.slice(0, n - 1) + "…" : t;
  }

  // Extract [entity] and {arc} tags from a link's text, deduped,
  // in order of first appearance. Used as the MENTIONS row when
  // buying a Link.
  function extractTagsFromText(text) {
    const out = [];
    const seen = new Set();
    const push = (key, label) => { if (!seen.has(key)) { seen.add(key); out.push(label); } };
    const re1 = /\[([a-z0-9-]+)\]/g;
    const re2 = /\{([a-z0-9-]+)\}/g;
    let m;
    while ((m = re1.exec(text || "")) !== null) push("e:" + m[1], "[" + m[1] + "]");
    while ((m = re2.exec(text || "")) !== null) push("a:" + m[1], "{" + m[1] + "}");
    return out;
  }

  // Fetch per-kind extras for the BUY preview. Creator/owner are taken
  // from the caller's `display` when provided; otherwise left blank.
  async function fetchBuyDetails(kind, nativeId, display) {
    const out = {
      creatorAddr: display && display.creatorAddr,
      creatorName: display && display.creatorName,
      ownerAddr:   display && display.ownerAddr,
      ownerName:   display && display.ownerName,
      content:     "",
      mentions:    [],
    };

    if (kind === "link") {
      const resp = await SprawlAPI.link(nativeId);
      if (resp) {
        out.content = resp.text || "";
        if (!out.creatorAddr && resp.author) out.creatorAddr = resp.author;
        out.mentions = extractTagsFromText(out.content);
      }
    } else if (kind === "entity") {
      const [entity, mentions] = await Promise.all([
        SprawlAPI.entity(nativeId),
        SprawlAPI.entityMentions(nativeId, 50).catch(() => null),
      ]);
      if (entity) {
        out.content = entity.description || "";
        if (!out.creatorAddr && entity.creator) out.creatorAddr = entity.creator;
      }
      if (mentions && Array.isArray(mentions.items)) {
        out.mentions = mentions.items.map(m => "#" + m.linkId);
      }
    } else if (kind === "arc") {
      const [arc, refs] = await Promise.all([
        SprawlAPI.arc(nativeId),
        SprawlAPI.arcReferences(nativeId, 50).catch(() => null),
      ]);
      if (arc) {
        out.content = arc.description || "";
        if (!out.creatorAddr && arc.creator) out.creatorAddr = arc.creator;
      }
      if (refs && Array.isArray(refs.items)) {
        out.mentions = refs.items.map(r => "#" + r.linkId);
      }
    }
    return out;
  }

  // Compose the rows the preview renders. Order: KIND → ID →
  // CONTENT (stacked, if any) → CREATOR → OWNER → MENTIONS → PRICE.
  // Each optional row is skipped when its data is missing.
  function buildBuyRows(kind, nativeId, priceBig, d) {
    const rows = [];
    rows.push(sprawlModal.row("KIND", KIND_LABEL[kind]));
    rows.push(sprawlModal.row("ID",   escapeHtml(idLabel(kind, nativeId))));

    if (d.content) {
      rows.push(sprawlModal.rowFull("CONTENT", escapeHtml(truncate(d.content, 600))));
    }
    if (d.creatorAddr) {
      rows.push(sprawlModal.row("CREATOR", escapeHtml(formatAuthor(d.creatorName || d.creatorAddr))));
    }
    if (d.ownerAddr) {
      rows.push(sprawlModal.row("OWNER", escapeHtml(formatAuthor(d.ownerName || d.ownerAddr))));
    }
    if (d.mentions && d.mentions.length) {
      // Cap at 10 so the MENTIONS row stays on a single line in the
      // wide modal; the excess count is surfaced as "(+N more)" and
      // the full list is one click away on the item's own page.
      const CAP = 10;
      const shown = d.mentions.slice(0, CAP).join(", ");
      const more  = d.mentions.length > CAP ? ` (+${d.mentions.length - CAP} more)` : "";
      const mentionsRow = sprawlModal.row("MENTIONS", escapeHtml(shown + more));
      mentionsRow.classList.add("oneline");
      rows.push(mentionsRow);
    }
    const priceRow = sprawlModal.row("PRICE", formatEth(priceBig));
    priceRow.classList.add("spaced-above");
    rows.push(priceRow);
    return rows;
  }

  // --- LIST ---
  // Shows a price input. Confirms with the typed ETH amount. Rejects
  // zero or malformed values inline before submitting.
  async function listAsset(kind, nativeId, display) {
    const err = await ensureReady();
    if (err) return showError(err);

    const k = String(kind).toLowerCase();

    const priceInput = document.createElement("input");
    priceInput.className = "modal-input";
    priceInput.type = "text";
    priceInput.placeholder = "0.01";
    priceInput.inputMode = "decimal";
    priceInput.autocomplete = "off";
    priceInput.spellcheck = false;

    const priceRow = document.createElement("div");
    priceRow.className = "modal-row";
    const kLabel = document.createElement("span"); kLabel.className = "k"; kLabel.textContent = "LIST PRICE";
    const vSpan  = document.createElement("span"); vSpan.className = "v";
    vSpan.appendChild(priceInput);
    vSpan.insertAdjacentText("beforeend", " ETH");
    priceRow.appendChild(kLabel); priceRow.appendChild(vSpan);

    const errLine = document.createElement("div");
    errLine.className = "modal-text muted";
    errLine.style.display = "none";

    const body = document.createElement("div");
    body.appendChild(sprawlModal.row("KIND", KIND_LABEL[k]));
    body.appendChild(sprawlModal.row("ID",   escapeHtml(idLabel(k, nativeId))));
    body.appendChild(priceRow);
    body.appendChild(errLine);

    function submitList() {
      const raw = priceInput.value.trim();
      let priceWei;
      try { priceWei = ethers.parseEther(raw); } catch { priceWei = null; }
      if (priceWei == null || priceWei <= 0n) {
        errLine.textContent = "Enter a valid ETH amount greater than 0.";
        errLine.style.display = "block";
        return;
      }
      runConfirmed(
        async () => {
          const contract = await sprawlWallet.getContract();
          const id = assetIdBytes32(k, nativeId);
          return await contract.list(kindNumber(k), id, priceWei);
        },
        () => {
          window.dispatchEvent(new CustomEvent("sprawl:listed", {
            detail: { kind: k, nativeId: String(nativeId), priceWei: priceWei.toString() }
          }));
        },
        "Listed."
      );
    }

    sprawlModal.open({
      title: "LIST " + KIND_LABEL[k],
      body,
      buttons: [
        { label: "CANCEL", kind: "muted", onClick: () => sprawlModal.close() },
        { label: "LIST",   kind: "primary", onClick: submitList },
      ],
    });

    setTimeout(() => priceInput.focus(), 50);
    priceInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); submitList(); }
    });
  }

  // --- UNLIST ---
  async function unlistAsset(kind, nativeId, display) {
    const err = await ensureReady();
    if (err) return showError(err);

    const k = String(kind).toLowerCase();
    const rows = [
      sprawlModal.row("KIND", KIND_LABEL[k]),
      sprawlModal.row("ID",   escapeHtml(idLabel(k, nativeId))),
    ];

    await runTxFlow({
      title: "UNLIST " + KIND_LABEL[k],
      previewRows: rows,
      confirmLabel: "UNLIST",
      successText: "Unlisted.",
      txFn: async () => {
        const contract = await sprawlWallet.getContract();
        const id = assetIdBytes32(k, nativeId);
        return await contract.unlist(kindNumber(k), id);
      },
      onSuccess: () => {
        window.dispatchEvent(new CustomEvent("sprawl:unlisted", {
          detail: { kind: k, nativeId: String(nativeId) }
        }));
      },
    });
  }

  // --- REGISTER ---
  // Registers the connected wallet as a Sprawl writer. Reads the current
  // registrationFee() from the contract, collects a name from a small
  // modal form, then calls register(name, { value: fee }). On success
  // we reload the current page so the (usually author.html) profile
  // layout flips to the "registered" version.
  //
  // Name validation is intentionally light on the client: the contract
  // enforces `non-empty` + `<= 64 bytes`. We mirror those here so the
  // user gets immediate feedback; anything else (e.g., already-registered)
  // falls through and surfaces via the normal error path.
  const MAX_NAME_BYTES = 64;
  async function register(prefill) {
    const err = await ensureReady();
    if (err) return showError(err);

    sprawlModal.open({
      title: "REGISTER",
      body: `<div class="modal-text muted">Loading fee…</div>`,
      buttons: [{ label: "CANCEL", kind: "muted", onClick: () => sprawlModal.close() }],
    });

    let fee;
    try {
      const read = sprawlWallet.getReadContract();
      fee = await read.registrationFee();
    } catch (e) {
      sprawlModal.setBody(`<div class="modal-text">Could not read the registration fee: ${escapeHtml(formatErr(e))}</div>`);
      sprawlModal.setButtons([{ label: "CLOSE", kind: "muted", onClick: () => sprawlModal.close() }]);
      return;
    }

    const nameInput = document.createElement("input");
    nameInput.className = "modal-input";
    nameInput.type = "text";
    nameInput.placeholder = "your-writer-name";
    nameInput.autocomplete = "off";
    nameInput.spellcheck = false;
    if (prefill) nameInput.value = prefill;
    // Matches the contract constant MAX_NAME_BYTES. Keeping it in sync
    // is a minor risk; if the contract raises this, the register tx
    // will succeed after the UI caps.
    nameInput.maxLength = MAX_NAME_BYTES;
    nameInput.style.width = "200px";
    nameInput.style.textAlign = "left";

    const nameRow = document.createElement("div");
    nameRow.className = "modal-row";
    const nK = document.createElement("span"); nK.className = "k"; nK.textContent = "NAME";
    const nV = document.createElement("span"); nV.className = "v"; nV.appendChild(nameInput);
    nameRow.appendChild(nK); nameRow.appendChild(nV);

    const feeRow = sprawlModal.row("FEE", formatEth(fee));
    feeRow.classList.add("spaced-above");

    const errLine = document.createElement("div");
    errLine.className = "modal-text muted";
    errLine.style.display = "none";

    const body = document.createElement("div");
    body.appendChild(nameRow);
    body.appendChild(feeRow);
    body.appendChild(errLine);

    function submitRegister() {
      const raw = nameInput.value.trim();
      // Byte length matters since the contract measures in bytes of the
      // UTF-8 encoding, not chars.
      const byteLen = new TextEncoder().encode(raw).length;
      if (!raw)               { errLine.textContent = "Pick a name.";                                           errLine.style.display = "block"; return; }
      if (byteLen > MAX_NAME_BYTES) { errLine.textContent = `Name too long (${byteLen} bytes, max ${MAX_NAME_BYTES}).`; errLine.style.display = "block"; return; }
      runConfirmed(
        async () => {
          const contract = await sprawlWallet.getContract();
          return await contract.register(raw, { value: fee });
        },
        () => {
          window.dispatchEvent(new CustomEvent("sprawl:registered", {
            detail: { address: sprawlWallet.getAddress(), name: raw }
          }));
        },
        "Registered."
      );
    }

    sprawlModal.setBody(body);
    sprawlModal.setButtons([
      { label: "CANCEL",   kind: "muted",   onClick: () => sprawlModal.close() },
      { label: "REGISTER", kind: "primary", onClick: submitRegister },
    ]);
    setTimeout(() => nameInput.focus(), 50);
    nameInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); submitRegister(); }
    });
  }

  // --- VOTE ---
  // Frontend voting mirrors the kit's write.py vote flow:
  //   1. Fetch the voter's current on-chain nonce from /citizens/{addr}
  //      (off-chain-managed on the server side) and bump by 1.
  //   2. Ask the wallet's provider for the latest Ethereum block number
  //      — the server's beacon-freshness gate rejects stale references.
  //   3. sign the EIP-712 Vote struct with the wallet's Signer.
  //   4. POST to /votes. The server verifies the sig, co-signs, stores
  //      the vote, and atomically increments the link's voteCount.
  //
  // No on-chain transaction, no gas. The only cost is a wallet signature.
  // Caller-side preconditions (hide VOTE button if already voted) are
  // enforced by the page; this function is the mechanism.
  const SPRAWL_DOMAIN = {
    name: "Sprawl",
    version: "1",
    chainId: sprawlWallet.SEPOLIA_CHAIN_ID,
    verifyingContract: sprawlWallet.CONTRACT_ADDRESS,
  };
  const VOTE_TYPES = {
    Vote: [
      { name: "linkId",      type: "uint256" },
      { name: "votedAt",     type: "uint64"  },
      { name: "nonce",       type: "uint64"  },
      { name: "beaconBlock", type: "uint64"  },
      { name: "voter",       type: "address" },
    ],
  };

  async function vote(linkId) {
    const err = await ensureReady();
    if (err) return showError(err);

    const voter = sprawlWallet.getAddress();
    if (!voter) return showError("Wallet not connected.");

    sprawlModal.open({
      title: "VOTE",
      body: `<div class="modal-text muted">Preparing vote…</div>`,
      buttons: [{ label: "CANCEL", kind: "muted", onClick: () => sprawlModal.close() }],
    });

    // Grab the voter's last nonce + the current block in parallel.
    let citizen, blockNumber;
    try {
      const provider = sprawlWallet.getProvider();
      [citizen, blockNumber] = await Promise.all([
        fetch(`${window.SPRAWL_API_URL || "https://d1pdbr4fdk59bz.cloudfront.net"}/citizens/${voter.toLowerCase()}`).then(r => r.ok ? r.json() : null),
        provider.getBlockNumber(),
      ]);
    } catch (e) {
      sprawlModal.setBody(`<div class="modal-text">Could not prepare vote: ${escapeHtml(formatErr(e))}</div>`);
      sprawlModal.setButtons([{ label: "CLOSE", kind: "muted", onClick: () => sprawlModal.close() }]);
      return;
    }
    if (!citizen) {
      sprawlModal.setBody(`<div class="modal-text">You need to register as an author before voting.</div>`);
      sprawlModal.setButtons([{ label: "CLOSE", kind: "muted", onClick: () => sprawlModal.close() }]);
      return;
    }

    const nonce = Number(citizen.lastNonce || 0) + 1;
    const votedAt = Math.floor(Date.now() / 1000);
    const msg = {
      linkId:      BigInt(linkId),
      votedAt:     votedAt,
      nonce:       nonce,
      beaconBlock: blockNumber,
      voter:       voter,
    };

    // Preview — show what's being signed, let the user confirm.
    const rows = [
      sprawlModal.row("LINK",  escapeHtml("#" + String(linkId))),
      sprawlModal.row("VOTER", escapeHtml(shortAddr(voter))),
    ];
    const hint = document.createElement("div");
    hint.className = "modal-text muted";
    hint.textContent = "Voting is free. You'll sign a message in your wallet — no transaction, no gas.";

    const body = document.createElement("div");
    for (const r of rows) body.appendChild(r);
    body.appendChild(hint);

    sprawlModal.setBody(body);
    sprawlModal.setButtons([
      { label: "CANCEL",  kind: "muted",   onClick: () => sprawlModal.close() },
      { label: "CONFIRM", kind: "primary", onClick: async () => {
        sprawlModal.setBody(`<div class="modal-text muted">Check your wallet to sign the vote.</div>`);
        sprawlModal.setButtons([]);

        let sig;
        try {
          const signer = await sprawlWallet.getSigner();
          sig = await signer.signTypedData(SPRAWL_DOMAIN, VOTE_TYPES, msg);
        } catch (e) {
          sprawlModal.setBody(`<div class="modal-text">${escapeHtml(formatErr(e))}</div>`);
          sprawlModal.setButtons([{ label: "CLOSE", kind: "muted", onClick: () => sprawlModal.close() }]);
          return;
        }

        sprawlModal.setBody(`<div class="modal-text muted">Submitting vote…</div>`);

        try {
          const apiUrl = (window.SPRAWL_API_URL || "https://d1pdbr4fdk59bz.cloudfront.net").replace(/\/$/, "");
          const resp = await fetch(apiUrl + "/votes", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              linkId:      "0x" + BigInt(linkId).toString(16),
              votedAt,
              nonce,
              beaconBlock: blockNumber,
              voter,
              authorSig: sig,
            }),
          });
          if (!resp.ok) {
            const errBody = await resp.text().catch(() => "");
            throw new Error(`votes endpoint returned ${resp.status}: ${errBody.slice(0, 200)}`);
          }
        } catch (e) {
          sprawlModal.setBody(`<div class="modal-text">${escapeHtml(formatErr(e))}</div>`);
          sprawlModal.setButtons([{ label: "CLOSE", kind: "muted", onClick: () => sprawlModal.close() }]);
          return;
        }

        sprawlModal.setBody(`<div class="modal-text">Vote recorded.</div>`);
        sprawlModal.setButtons([{ label: "CLOSE", kind: "primary", onClick: () => sprawlModal.close() }]);

        window.dispatchEvent(new CustomEvent("sprawl:voted", {
          detail: { linkId: String(linkId), voter }
        }));
      }},
    ]);
  }

  // --- WITHDRAW ---
  // Claim ETH credited via pendingWithdrawals. We read the amount first
  // so the user sees the number before signing; we also use it to
  // short-circuit when there's nothing to claim.
  async function withdraw() {
    const err = await ensureReady();
    if (err) return showError(err);

    let pending;
    try {
      const read = sprawlWallet.getReadContract();
      pending = await read.pendingWithdrawals(sprawlWallet.getAddress());
    } catch (e) { return showError("Could not read your pending balance: " + formatErr(e)); }

    if (pending === 0n) return showError("Nothing to withdraw.");

    const rows = [sprawlModal.row("AMOUNT", formatEth(pending))];

    await runTxFlow({
      title: "WITHDRAW",
      previewRows: rows,
      confirmLabel: "WITHDRAW",
      successText: "Withdrawn.",
      txFn: async () => {
        const contract = await sprawlWallet.getContract();
        return await contract.withdraw();
      },
      onSuccess: () => {
        window.dispatchEvent(new CustomEvent("sprawl:withdrew", {
          detail: { address: sprawlWallet.getAddress() }
        }));
      },
    });
  }

  window.sprawlActions = {
    collect,
    buy,
    list:     listAsset,
    unlist:   unlistAsset,
    withdraw,
    register,
    vote,
    // Helpers exposed for page-level use (e.g., reading pending balance).
    formatEth, shortAddr, idLabel, assetIdBytes32, kindNumber,
  };
})();
