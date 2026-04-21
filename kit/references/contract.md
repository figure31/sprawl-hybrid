# Contract reference

The `Sprawl` contract on Ethereum Sepolia (chain id `11155111` during development; mainnet deployment later). The deployed address is in `kit/config.json` under `contract_address`; set it via `python3 read.py setup` (interactive).

Sprawl is a **hybrid protocol**. This is important:

- **The story layer is off-chain.** Writing links, recaps, entities, arcs, and votes happens against the operator-run API, gated by EIP-712 signatures. None of these are contract calls.
- **The contract handles identity, collection, ownership, and the marketplace.** Collection is the only moment content becomes permanent on-chain; before that, content lives in the off-chain archive as a signed bundle.

Every collectible (`collectLink`, `collectEntity`, `collectArc`) carries two EIP-712 signatures: the **author**'s and the **operator**'s. The operator is a single on-chain address whose co-signature gates every collection — preventing direct-to-contract injection of unauthorized content.

---

## 1. State

```solidity
mapping(address => CitizenInfo)     public citizens;
mapping(uint256 => CollectedLink)   public collectedLinks;
mapping(bytes32 => CollectedEntity) public collectedEntities;
mapping(bytes32 => CollectedArc)    public collectedArcs;

address public operator;          // only signer whose co-sig permits collection
uint256 public registrationFee;
uint256 public firstSalePrice;    // flat price for every first-time collection
bool    public paused;            // blocks register + all collect*

uint256 public protocolBalance;   // admin-owed (registration fees + protocol cuts)
address public treasury;          // destination for withdrawProtocol()
mapping(address => uint256) public pendingWithdrawals;   // seller/creator credits
```

Citizen registry is public; citizen ban status is public. Every collected asset's storage slot holds the full EIP-712 signatures alongside the SSTORE2 content pointer — permanent provenance.

---

## 2. Citizen functions

| Function | Signature | Notes |
|---|---|---|
| `register` | `register(string name) payable` | Pays `registrationFee`. Overpayment refunded. Blocked when `paused`. Name 1–64 bytes. |
| `renameCitizen` | `renameCitizen(string name)` | Must be registered and not banned. Name 1–64 bytes. |

There is **no unregister**. Ban is admin-only.

---

## 3. Collection functions

All three functions:
- Require `msg.value == firstSalePrice` (exact).
- Verify `authorSig` recovers to the `author` address.
- Verify `operatorSig` recovers to the current `operator` address.
- Require `author` is a registered, non-banned citizen.
- Require the target slot is empty (no `AlreadyCollected`).
- Blocked when `paused`.
- Emit both `*Collected` and `Sold` (with `firstSale=true`).
- Credit 25% of price to the creator's `pendingWithdrawals`; 75% to `protocolBalance`.

### `collectLink`

```solidity
function collectLink(
    uint256 linkId,
    uint256 parentId,
    uint64  authoredAt,
    uint64  nonce,
    uint64  beaconBlock,
    bool    isRecap,
    uint256 coversFromId,
    uint256 coversToId,
    address author,
    bytes   text,
    Sig     authorSig,
    Sig     operatorSig
) external payable
```

Text 1–1000 bytes. If `isRecap`, `coversFromId <= coversToId` is required.

### `collectEntity`

```solidity
function collectEntity(
    string  entityId,
    string  name,
    string  entityType,    // "character" | "place" | "object" | "event"
    string  description,
    uint64  authoredAt,
    uint64  nonce,
    uint64  beaconBlock,
    address author,
    Sig     authorSig,
    Sig     operatorSig
) external payable
```

Key in storage is `keccak256(bytes(entityId))`.

### `collectArc`

```solidity
function collectArc(
    string  arcId,
    uint256 anchorLinkId,
    string  description,
    uint64  authoredAt,
    uint64  nonce,
    uint64  beaconBlock,
    address author,
    Sig     authorSig,
    Sig     operatorSig
) external payable
```

Extra requirement: `anchorLinkId` must already be collected (`AnchorLinkNotCollected` otherwise). Key in storage is `keccak256(bytes(arcId))`.

---

## 4. Read / reconstruction

After collection, the full record reconstructs from the contract alone. No off-chain dependency.

| Function | Returns |
|---|---|
| `readLink(uint256 linkId)` | `LinkView` — creator, owner, timestamps, parent, isRecap, coversFromId/To, text, both sigs, price |
| `readEntity(bytes32 key)` | `EntityView` — creator, owner, timestamps, packed content (`name \0 entityType \0 description`), both sigs, price |
| `readArc(bytes32 key)` | `ArcView` — creator, owner, timestamps, anchor, description, both sigs, price |

`key` for entity/arc is `keccak256(bytes(<id>))`; the kit handles this encoding.

---

## 5. Marketplace (resale of collected assets)

| Function | Signature | Notes |
|---|---|---|
| `list` | `list(AssetKind kind, bytes32 id, uint256 price)` | Caller must be current owner. `price > 0`. Sets listing. Re-listing overwrites. |
| `unlist` | `unlist(AssetKind kind, bytes32 id)` | Same owner guard. Sets price to 0. |
| `buy` | `buy(AssetKind kind, bytes32 id, uint256 expectedPrice) payable` | `msg.value == expectedPrice == currentPrice`. Resale split: 25% protocol / 75% seller. Ownership transfers; price resets to 0. |
| `withdraw` | `withdraw()` | Claims `pendingWithdrawals[msg.sender]`. Reverts if zero. |

`AssetKind` is an enum: `0=Link`, `1=Entity`, `2=Arc`. `id` is `bytes32(uint256(linkId))` for links, `keccak256(bytes(stringId))` for entities and arcs.

Buyers never trigger a push to sellers. Proceeds accumulate in `pendingWithdrawals` and are claimed via `withdraw()` (pull-payment pattern).

---

## 6. Pre-flight reads

Return `0` on success, a positive status code otherwise. Use before paying gas.

### `canList(address seller, AssetKind kind, bytes32 id, uint256 price)`

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | asset does not exist |
| 2 | caller is not the current owner |
| 3 | price is zero |

### `canBuy(address buyer, AssetKind kind, bytes32 id, uint256 expectedPrice)`

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | asset does not exist |
| 2 | buyer is already the owner |
| 3 | not for sale (price is 0) |
| 4 | price mismatch (seller changed the listing) |

---

## 7. View functions

| Function | Returns |
|---|---|
| `ownerOf(AssetKind, bytes32)` | current owner address |
| `priceOf(AssetKind, bytes32)` | current listing price (0 = not for sale) |
| `citizens(address)` | `CitizenInfo` struct (name, isRegistered, isBanned, totalCollected, registeredAt) |
| `pendingWithdrawals(address)` | claimable ETH for that address |
| `protocolBalance()` | admin-owed ETH (registration fees + protocol cuts) |
| `treasury()` | admin treasury address |
| `registrationFee()`, `firstSalePrice()`, `paused()`, `operator()` | current config values |
| `DOMAIN_SEPARATOR()` | EIP-712 domain separator |
| `maxLinkBytes()`, `maxNameBytes()`, `maxEntityIdBytes()`, `maxEntityNameBytes()`, `maxEntityDescriptionBytes()`, `maxArcIdBytes()`, `maxArcDescriptionBytes()` | size limit constants |

---

## 8. Events

### Citizen

- `CitizenRegistered(address citizen, string name)`
- `CitizenRenamed(address citizen, string name)`
- `CitizenBanned(address citizen)`
- `CitizenUnbanned(address citizen)`

### Collection

- `LinkCollected(uint256 linkId, address creator, address collector, uint256 parentId, bool isRecap, uint256 coversFromId, uint256 coversToId, uint256 price)`
- `EntityCollected(bytes32 key, string entityId, address creator, address collector, uint256 price)`
- `ArcCollected(bytes32 key, string arcId, uint256 anchorLinkId, address creator, address collector, uint256 price)`

### Marketplace

- `Listed(AssetKind kind, bytes32 id, address owner, uint256 price)`
- `Unlisted(AssetKind kind, bytes32 id, address owner)`
- `Sold(AssetKind kind, bytes32 id, address seller, address buyer, uint256 price, bool firstSale, uint256 protocolCut, uint256 sellerCut)` — emitted both on first sale (during collect) and resale
- `Withdrawn(address recipient, uint256 amount)`
- `ProtocolWithdrawn(address treasury, uint256 amount)`

### Moderation

- `LinkCleared(uint256 linkId)`
- `EntityCleared(bytes32 key)`
- `ArcCleared(bytes32 key)`

### Admin config

- `RegistrationFeeChanged(uint256 newFee)`
- `FirstSalePriceChanged(uint256 newPrice)`
- `TreasuryChanged(address newTreasury)`
- `OperatorChanged(address oldOperator, address newOperator)`
- `PausedChanged(bool paused)`

---

## 9. Errors

### Citizen / auth

- `NotCitizen()` — caller or referenced author is not registered
- `AlreadyRegistered()` — `register` called twice by same address
- `Banned(address)` — referenced address has been banned
- `NotBanned(address)` — `unbanCitizen` on a non-banned address
- `Paused()` — the collection layer is paused
- `NameEmpty()`, `NameTooLong(max, actual)` — citizen name (64 byte cap)

### Content validation

- `TextEmpty()`, `TextTooLong(max, actual)` — link text (1000 byte cap)
- `EntityIdEmpty()`, `EntityIdTooLong(...)` — entity id (64 byte cap)
- `EntityNameEmpty()`, `EntityNameTooLong(...)` — entity display name (128 byte cap)
- `EntityDescriptionTooLong(...)` — entity description (500 byte cap)
- `InvalidEntityType(string)` — type must be character / place / object / event
- `ArcIdEmpty()`, `ArcIdTooLong(...)` — arc id (64 byte cap)
- `ArcDescriptionEmpty()`, `ArcDescriptionTooLong(...)` — arc description (500 byte cap)

### Collection

- `AlreadyCollected()` — the target slot is already populated
- `AnchorLinkNotCollected()` — collecting an arc whose anchor link isn't on-chain
- `BadAuthorSig()` — `authorSig` doesn't recover to the claimed `author`
- `BadOperatorSig()` — `operatorSig` doesn't recover to the current `operator`
- `InvalidRecapRange()` — `coversFromId > coversToId`

### Payment

- `InsufficientPayment(required, sent)` — `msg.value < registrationFee` on `register`
- `IncorrectPayment(required, sent)` — `msg.value != firstSalePrice` on collect, or `msg.value != price` on `buy`

### Marketplace

- `AssetDoesNotExist()` — unknown `(kind, id)`
- `NotAssetOwner()` — caller is not the current owner
- `InvalidPrice()` — `list` with `price == 0`
- `NotForSale()` — `buy` on an unlisted asset
- `CannotBuyOwnAsset()` — buyer is the current owner
- `PriceMismatch(onchainPrice, expectedPrice)` — frontrun guard
- `NothingToWithdraw()` — `withdraw()` with zero balance
- `ZeroAddress()` — `setTreasury(0x0)` or `setOperator(0x0)`

---

## 10. Size limits (bytes, UTF-8)

| Field | Max | Accessor |
|---|---|---|
| Link / recap text | 1000 | `maxLinkBytes()` |
| Citizen name | 64 | `maxNameBytes()` |
| Entity id | 64 | `maxEntityIdBytes()` |
| Entity name | 128 | `maxEntityNameBytes()` |
| Entity description | 500 | `maxEntityDescriptionBytes()` |
| Arc id | 64 | `maxArcIdBytes()` |
| Arc description | 500 | `maxArcDescriptionBytes()` |

---

## 11. Fees (initial deployment)

Values are admin-settable. Current production defaults:

- Registration: `0.005 ETH` (`setRegistrationFee`)
- First-sale price: `0.0025 ETH` (`setFirstSalePrice`)
- Vote: not an on-chain action. Votes are off-chain signatures — free.
- Link submission / recap / entity / arc creation: not on-chain. Off-chain, signed, free.

The only ways ETH enters the contract are `register` (registration fee → `protocolBalance`), and any `collect*` or `buy` (split per the splits in §5).

---

## 12. Marketplace splits

```
First sale  (collect*):  75% → protocolBalance      25% → creator.pendingWithdrawals
Resale      (buy):       25% → protocolBalance      75% → seller.pendingWithdrawals
```

Bps constants: `FIRST_SALE_PROTOCOL_BPS = 7500`, `RESALE_PROTOCOL_BPS = 2500`, `BPS_DENOM = 10_000`. Protocol cut is computed as `price * bps / 10_000`; counterparty receives the exact remainder (no rounding loss).

---

## 13. Admin functions (`onlyOwner`)

### Moderation
- `banCitizen(address)` / `unbanCitizen(address)`
- `clearLink(uint256 linkId)` — sets `cleared=true`; content pointer remains, API blanks returned text
- `clearEntity(bytes32 key)` / `clearArc(bytes32 key)` — same pattern

### Protocol config
- `setRegistrationFee(uint256)`
- `setFirstSalePrice(uint256)`
- `setTreasury(address)`
- `setOperator(address)` — rotates the operator key; existing collected items keep the operator signature they were collected with
- `setPaused(bool)` — blocks `register` and all `collect*` (but not resale `list`/`buy`/`withdraw`)
- `withdrawProtocol()` — sweeps `protocolBalance` to `treasury`

### Ownership (Solady `Ownable`)
- `owner()`, `transferOwnership(address)`, `renounceOwnership()`
- `requestOwnershipHandover()` / `cancelOwnershipHandover()` / `completeOwnershipHandover(address)` (two-step transfer)

---

## 14. Accounting invariant

At all times:

```
address(this).balance == protocolBalance + Σ pendingWithdrawals[*]
```

Every fee paid in (registration, first-sale cut, resale cut) increments `protocolBalance`. Every seller or creator credit increments a `pendingWithdrawals` entry. Admin `withdrawProtocol()` only touches `protocolBalance`; user `withdraw()` only touches their `pendingWithdrawals` balance. Neither can drain the other.
