/* =============================================================
   Sprawl — shared wallet manager.

   Wraps window.ethereum into a small state machine + event bus:
     - address   : lowercase hex string or null when disconnected
     - chainId   : decimal chain id number or null
     - connected : address != null
     - onSepolia : chainId === 11155111  (Sepolia rehearsal of the
                                            mainnet-shape contract)

   Emits a single "change" callback with the full state whenever
   any field moves. Pages subscribe via sprawlWallet.on(cb); the
   topbar wiring in sprawl-boot-wallet.js uses this to update the
   CONNECT / address link globally.

   The manager itself doesn't render UI. It exposes helpers for
   the action flows:
     connect()          — prompt for accounts (eth_requestAccounts)
     forget()           — clear local state (site permission remains)
     switchToSepolia()  — wallet_switchEthereumChain (adds on 4902)
     getProvider()      — ethers.BrowserProvider wrapping window.ethereum
     getSigner()        — ethers Signer (requires connected account)
     getContract()      — ethers Contract wired with signer
     getReadContract()  — read-only Contract wired with provider

   Assumes `ethers` v6 is loaded globally before this script runs
   (UMD build in each page's <head>).
   ============================================================= */
(function () {
  // Sepolia rehearsal target. After the rehearsal passes, flip the four
  // constants below to mainnet values + run the same fresh-deploy
  // checklist for the production deploy.
  const SEPOLIA_CHAIN_ID = 11155111;
  const SEPOLIA_HEX      = "0xaa36a7";
  // Sepolia rehearsal #2 — string-id marketplace, mutable resale premium,
  // signature malleability hardened. Deployed 2026-04-29, block 10756618.
  const CONTRACT_ADDRESS = "0xC56fE1CF937b3BbD3c675AFD20f0631F61A7c8D1";
  const ETHERSCAN_TX     = "https://sepolia.etherscan.io/tx/";
  const ETHERSCAN_ADDR   = "https://sepolia.etherscan.io/address/";

  // Minimal human-readable ABI. Sig struct is {bytes32 r, bytes32 s, uint8 v}
  // per the contract — order matters since we pass tuples positionally.
  // The marketplace functions (buy/list/unlist) take the asset's
  // human-readable id as a `string` (e.g., "123" / "the-procedure" /
  // "adam-journey"). The contract converts to its bytes32 storage key
  // internally so decoded tx inputs stay readable on Etherscan.
  const SPRAWL_ABI = [
    "function register(string name) payable",
    "function collectLink(uint256 linkId, uint256 parentId, uint64 authoredAt, uint64 nonce, uint64 beaconBlock, address author, bytes text, (bytes32,bytes32,uint8) authorSig, (bytes32,bytes32,uint8) operatorSig) payable",
    "function collectEntity(string entityId, string entityType, string description, uint64 authoredAt, uint64 nonce, uint64 beaconBlock, address author, (bytes32,bytes32,uint8) authorSig, (bytes32,bytes32,uint8) operatorSig) payable",
    "function collectArc(string arcId, uint256 anchorLinkId, string description, uint64 authoredAt, uint64 nonce, uint64 beaconBlock, address author, (bytes32,bytes32,uint8) authorSig, (bytes32,bytes32,uint8) operatorSig) payable",
    "function buy(uint8 kind, string id, uint256 expectedPrice) payable",
    "function list(uint8 kind, string id, uint256 price)",
    "function unlist(uint8 kind, string id)",
    "function withdraw()",
    "function firstSalePrice() view returns (uint256)",
    "function registrationFee() view returns (uint256)",
    "function resalePremiumBps() view returns (uint256)",
    "function pendingWithdrawals(address) view returns (uint256)",
  ];

  const state = { address: null, chainId: null, connecting: false };
  const listeners = new Set();

  // Sticky "disconnected" flag. When the user clicks DISCONNECT, we set
  // this in localStorage; init() then skips the silent eth_accounts
  // check on subsequent loads so the wallet stays disconnected even
  // across reloads. Calling connect() clears the flag.
  const DISCONNECT_KEY = "sprawl-wallet-disconnected";
  function isDisconnectSticky() {
    try { return localStorage.getItem(DISCONNECT_KEY) === "1"; }
    catch { return false; }
  }
  function setDisconnectSticky(on) {
    try {
      if (on) localStorage.setItem(DISCONNECT_KEY, "1");
      else    localStorage.removeItem(DISCONNECT_KEY);
    } catch { /* private mode, etc. */ }
  }

  function snapshot() {
    return {
      address:   state.address,
      chainId:   state.chainId,
      connected: !!state.address,
      onSepolia: state.chainId === SEPOLIA_CHAIN_ID,
      connecting: state.connecting,
    };
  }
  function emit() { for (const cb of listeners) { try { cb(snapshot()); } catch {} } }

  async function init() {
    if (!window.ethereum) { emit(); return; }

    // Silent account check unless the user previously asked us to stay
    // disconnected. We still read the chain id in the sticky case so
    // future interactions know which network the wallet is on.
    if (!isDisconnectSticky()) {
      try {
        const accounts = await window.ethereum.request({ method: "eth_accounts" });
        const chainId  = await window.ethereum.request({ method: "eth_chainId" });
        state.address = (accounts && accounts[0]) ? accounts[0].toLowerCase() : null;
        state.chainId = chainId ? parseInt(chainId, 16) : null;
      } catch { /* treat as disconnected */ }
    } else {
      try {
        const chainId = await window.ethereum.request({ method: "eth_chainId" });
        state.chainId = chainId ? parseInt(chainId, 16) : null;
      } catch {}
    }

    if (window.ethereum.on) {
      window.ethereum.on("accountsChanged", (accounts) => {
        state.address = (accounts && accounts[0]) ? accounts[0].toLowerCase() : null;
        emit();
      });
      window.ethereum.on("chainChanged", (chainId) => {
        state.chainId = chainId ? parseInt(chainId, 16) : null;
        emit();
      });
    }
    emit();
  }

  async function connect() {
    if (!window.ethereum) {
      throw new Error("No wallet detected. Install MetaMask, Rabby, or a similar browser wallet.");
    }
    state.connecting = true; emit();
    try {
      const accounts = await window.ethereum.request({ method: "eth_requestAccounts" });
      const chainId  = await window.ethereum.request({ method: "eth_chainId" });
      state.address  = (accounts && accounts[0]) ? accounts[0].toLowerCase() : null;
      state.chainId  = chainId ? parseInt(chainId, 16) : null;
      // An explicit connect clears any sticky-disconnect flag from a
      // prior session — the user is opting back in.
      setDisconnectSticky(false);
    } finally {
      state.connecting = false; emit();
    }
    return state.address;
  }

  // In-memory forget. Used internally; disconnect() below is the public
  // "sign out of this site" entry point.
  function forget() {
    state.address = null;
    emit();
  }

  // Full disconnect: clears local state, sets the sticky flag so
  // silent reconnects don't happen on the next page load, and makes a
  // best-effort attempt to revoke the site's wallet permission via
  // EIP-2255 (supported by Metamask/Rabby, silently ignored by others).
  // The user can always call connect() to re-authorize.
  async function disconnect() {
    setDisconnectSticky(true);
    state.address = null;
    if (window.ethereum) {
      try {
        await window.ethereum.request({
          method: "wallet_revokePermissions",
          params: [{ eth_accounts: {} }],
        });
      } catch { /* not supported or user cancelled — sticky flag handles it */ }
    }
    emit();
  }

  async function switchToSepolia() {
    if (!window.ethereum) throw new Error("No wallet detected.");
    try {
      await window.ethereum.request({
        method: "wallet_switchEthereumChain",
        params: [{ chainId: SEPOLIA_HEX }],
      });
    } catch (e) {
      // 4902 = chain not yet added in the wallet; try to add it.
      if (e && e.code === 4902) {
        await window.ethereum.request({
          method: "wallet_addEthereumChain",
          params: [{
            chainId: SEPOLIA_HEX,
            chainName: "Sepolia",
            nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
            rpcUrls: ["https://rpc.sepolia.org"],
            blockExplorerUrls: ["https://sepolia.etherscan.io"],
          }],
        });
      } else { throw e; }
    }
  }

  function getProvider() {
    if (!window.ethereum) return null;
    if (typeof ethers === "undefined") return null;
    return new ethers.BrowserProvider(window.ethereum);
  }
  async function getSigner() {
    const p = getProvider();
    if (!p) return null;
    return await p.getSigner();
  }
  async function getContract() {
    const signer = await getSigner();
    if (!signer) return null;
    return new ethers.Contract(CONTRACT_ADDRESS, SPRAWL_ABI, signer);
  }
  function getReadContract() {
    const provider = getProvider();
    if (!provider) return null;
    return new ethers.Contract(CONTRACT_ADDRESS, SPRAWL_ABI, provider);
  }

  function shortAddr(addr) {
    if (!addr) return "";
    return addr.slice(0, 6) + "…" + addr.slice(-4);
  }

  function on(cb) { listeners.add(cb); }
  function off(cb) { listeners.delete(cb); }

  window.sprawlWallet = {
    SEPOLIA_CHAIN_ID,
    CONTRACT_ADDRESS,
    ETHERSCAN_TX,
    ETHERSCAN_ADDR,
    getState: snapshot,
    getAddress: () => state.address,
    getChainId: () => state.chainId,
    isConnected: () => !!state.address,
    isOnSepolia: () => state.chainId === SEPOLIA_CHAIN_ID,
    connect, disconnect, forget, switchToSepolia,
    getProvider, getSigner, getContract, getReadContract,
    shortAddr,
    on, off,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
