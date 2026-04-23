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
    const btn = document.getElementById("wallet-connect");
    if (!btn) return;

    function render(state) {
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

    render(sprawlWallet.getState());
    sprawlWallet.on(render);

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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
  } else {
    setup();
  }
})();
