/* =============================================================
   Sprawl — topbar wallet-button wiring.

   Every page ships `<a id="wallet-connect" href="#">CONNECT</a>`
   in the topbar's .nav-right group. This script wires it:

     - Subscribes to wallet state changes (sprawlWallet.on)
     - Renders the label: "CONNECT" when disconnected,
       shortened address (uppercase) when connected
     - Click when disconnected → sprawlWallet.connect()
     - Click when connected    → navigate to author.html?addr=<self>
     - Click when connected but on wrong chain → switchToSepolia()
     - Adds a "WRONG NETWORK" indicator next to the address so it's
       obvious why actions are blocked

   Visual: matches the other .nav-right links. No new styles needed.
   ============================================================= */
(function () {
  function titleForState(state) {
    if (!state.connected) return "Connect your wallet";
    if (!state.onSepolia) return state.address + " — wrong network, click to switch to Sepolia";
    return state.address;
  }

  function setup() {
    // Support both the legacy `#wallet-connect` single-instance setup and
    // pages with multiple wallet buttons (e.g. index.html's mobile hamburger
    // menu, which needs a second CONNECT entry that mirrors the desktop
    // one). Any element tagged with id=wallet-connect OR class
    // js-wallet-connect participates; `querySelectorAll` returns each
    // element once even if it matches both selectors.
    const buttons = Array.from(
      document.querySelectorAll("#wallet-connect, .js-wallet-connect")
    );
    if (!buttons.length) return;

    function render(state) {
      for (const btn of buttons) {
        if (state.connected) {
          const short = sprawlWallet.shortAddr(state.address).toUpperCase();
          const suffix = state.onSepolia ? "" : " ⚠";
          btn.textContent = short + suffix;
          btn.href = "author.html?addr=" + encodeURIComponent(state.address);
        } else {
          btn.textContent = state.connecting ? "CONNECTING…" : "CONNECT";
          btn.href = "#";
        }
        btn.title = titleForState(state);
      }
    }

    render(sprawlWallet.getState());
    sprawlWallet.on(render);

    for (const btn of buttons) {
      btn.addEventListener("click", async (ev) => {
        const state = sprawlWallet.getState();
        if (!state.connected) {
          // Disconnected: open the wallet prompt.
          ev.preventDefault();
          try { await sprawlWallet.connect(); }
          catch (e) { /* user cancelled — stay disconnected */ }
          return;
        }
        if (!state.onSepolia) {
          // Connected but wrong chain: offer to switch instead of navigating.
          ev.preventDefault();
          try { await sprawlWallet.switchToSepolia(); } catch {}
          return;
        }
        // Connected + correct chain: let the href navigate to the profile.
      });
    }
  }

  // -------- Mobile hamburger --------
  // Shared across every page that includes a topbar + hamburger. Binds
  // the button to toggle .is-open on #nav-mobile (which CSS flips from
  // display:none → display:flex), morphs the icon into an X, closes on
  // Escape or after an intra-panel link tap.
  function setupHamburger() {
    const btn = document.getElementById("nav-hamburger");
    const panel = document.getElementById("nav-mobile");
    if (!btn || !panel) return;
    function isOpen() { return panel.classList.contains("is-open"); }
    function setOpen(open) {
      panel.classList.toggle("is-open", open);
      btn.classList.toggle("is-open", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    }
    btn.addEventListener("click", (e) => { e.preventDefault(); setOpen(!isOpen()); });
    panel.addEventListener("click", (e) => {
      if (e.target.closest("a")) setOpen(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && isOpen()) setOpen(false);
    });
  }

  // -------- Theme toggle (dark / light) --------
  // Every page used to duplicate this inline. Moved here so the mobile
  // hamburger panel's DARK/LIGHT copy (class .js-theme-toggle) stays in
  // sync with the desktop #theme-toggle button automatically.
  function setupTheme() {
    const btns = Array.from(document.querySelectorAll("#theme-toggle, .js-theme-toggle"));
    if (!btns.length) return;
    const sync = () => {
      const label = document.documentElement.classList.contains("dark") ? "LIGHT" : "DARK";
      btns.forEach(b => b.textContent = label);
    };
    sync();
    btns.forEach(btn => btn.addEventListener("click", (e) => {
      e.preventDefault();
      const next = !document.documentElement.classList.contains("dark");
      document.documentElement.classList.toggle("dark", next);
      try { localStorage.setItem("sprawl-theme", next ? "dark" : "light"); } catch {}
      sync();
    }));
  }

  function bootAll() {
    setup();
    setupHamburger();
    setupTheme();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootAll);
  } else {
    bootAll();
  }
})();
