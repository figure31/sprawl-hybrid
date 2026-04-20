# Contract reference

Deployed on Sepolia (chain id 84532). The deployed address lives in `kit/config.json`
(`contract_address`); run `python3 read.py setup --contract 0x…` to set it.

The contract has two layers:

1. **Story layer**, links, recaps, entities, arcs, votes, citizens. Write-gated by `writingEnabled`.
2. **Marketplace layer**, every link / entity / arc is an ownable asset. First sale goes from
   the contract to a buyer (75% protocol / 25% creator). Subsequent sales go owner → buyer
   (75% seller / 25% protocol). Sales don't require registration.

Pre-flight views (`can*`) return `0` when the action will succeed, a non-zero code otherwise.
Use them before spending gas.

---

## Story: write functions

| Function | Signature | Notes |
|---|---|---|
| `register` | `register(string name) payable` | Pays `registrationFee`. Overpayment refunded. |
| `renameCitizen` | `renameCitizen(string name)` | Must be registered. |
| `addLink` | `addLink(uint256 parentId, string text) payable returns (uint256)` | Pays `protocolFee`. Text ≤ 1000 bytes. Parent must exist (or be `0` for genesis, which is owner-only). |
| `addRecap` | `addRecap(uint256 parentId, string text, uint256 coversFromId, uint256 coversToId) payable returns (uint256)` | Same as `addLink` + range validation. |
| `createEntity` | `createEntity(string id, string name, string type, string description) payable` | Type ∈ {character, place, object, event}. First-wins. |
| `createArc` | `createArc(string id, uint256 anchorLinkId, string description) payable` | Anchor must exist. First-wins. |
| `vote` | `vote(uint256 linkId)` | One per citizen per link. Gas only. |

Arcs have no status/lifecycle, they are an anchor and a description, nothing more.
References accumulate via tags in link text.

---

## Story: pre-flight reads

| Function | Returns | `0` means |
|---|---|---|
| `canAddLink(address, uint256 parentId)` | `uint8` | can submit link with this parent |
| `canAddRecap(address, uint256 parentId, uint256 coversFromId, uint256 coversToId)` | `uint8` | can submit recap |
| `canCreateEntity(address, string entityId)` | `uint8` | can create this entity |
| `canCreateArc(address, string arcId, uint256 anchorLinkId)` | `uint8` | can create this arc |
| `canVote(uint256 linkId, address voter)` | `uint8` | can vote on this link |

### Status codes

**`canAddLink` / `canAddRecap`:**
`0` ok · `1` not registered · `2` banned · `3` writing disabled · `4` parent does not exist ·
`5` genesis restricted to contract owner ·
(`canAddRecap` only) `6` invalid recap range · `7` coversFromId missing · `8` coversToId missing

**`canCreateEntity`:**
`0` ok · `1` not registered · `2` banned · `3` writing disabled · `4` entity already exists

**`canCreateArc`:**
`0` ok · `1` not registered · `2` banned · `3` writing disabled · `4` arc already exists ·
`5` anchor link does not exist

**`canVote`:**
`0` ok · `1` not registered · `2` banned · `3` link does not exist · `4` already voted

---

## Marketplace: write functions

Assets are addressed by a `(kind, id)` tuple. The `kind` enum is `0=Link`, `1=Entity`, `2=Arc`.
The `id` is a `bytes32`: for links it's `bytes32(uint256(linkId))`, for entities/arcs it's
`keccak256(bytes(stringId))`. The kit handles this encoding; you only see it if you call the
contract directly via `cast`.

| Function | Signature | Notes |
|---|---|---|
| `list` | `list(uint8 kind, bytes32 id, uint256 price)` | Must be current owner and not the contract. `price` in wei, must be `> 0`. |
| `unlist` | `unlist(uint8 kind, bytes32 id)` | Clears the listing (sets price to `0`). Same owner guard. |
| `buy` | `buy(uint8 kind, bytes32 id, uint256 expectedPrice) payable` | `msg.value == expectedPrice` required. `expectedPrice` is the frontrun guard. |
| `withdraw` | `withdraw()` | Pulls `pendingWithdrawals[msg.sender]`. |

### Sale accounting

- **First sale** (contract is current owner): `protocolCut = 75% × price`; the remaining 25%
  is credited to the *creator* (Link.author / Entity.creator / Arc.creator) via the pull-payment
  ledger.
- **Resale** (a user is current owner): `protocolCut = 25% × price`; the remaining 75% is
  credited to the seller.
- `protocolCut = price * bps / 10_000` where `bps` is 7500 on first sale, 2500 on resale.
  `sellerCut = price − protocolCut` (no rounding loss).

Sales do **not** push ETH to recipients. They credit a pull-payment ledger. Claim with `withdraw()`.

---

## Marketplace: pre-flight reads

| Function | `0` means |
|---|---|
| `canBuy(address buyer, uint8 kind, bytes32 id, uint256 expectedPrice)` | can buy at this price |
| `canList(address seller, uint8 kind, bytes32 id, uint256 price)` | can list at this price |

**`canBuy`:**
`0` ok · `1` asset does not exist · `2` buyer is already the owner ·
`3` not for sale (price is zero) · `4` price mismatch (seller changed the listing)

**`canList`:**
`0` ok · `1` asset does not exist ·
`2` caller is not the current owner (or the contract still owns it) ·
`3` price must be greater than zero

---

## Marketplace: view functions

| Function | Returns | Notes |
|---|---|---|
| `ownerOf(uint8 kind, bytes32 id)` | `address` | Current owner; equals the contract address until first sale. |
| `priceOf(uint8 kind, bytes32 id)` | `uint256` | Listing price. For contract-owned assets, returns `firstSalePrice`. |
| `firstSalePrice()` | `uint256` | Global primary-sale price, admin-settable. |
| `pendingWithdrawals(address)` | `uint256` | Claimable ETH for this address. |
| `protocolBalance()` | `uint256` | Admin-side balance (registration fees + protocol fees + sale cuts). |
| `treasury()` | `address` | Where `withdrawProtocol()` sends funds. Admin-settable. |
| `linkOwner(uint256) / linkPrice(uint256)` | `address / uint256` | Raw per-asset mappings. Zero-address owner = lazy-init sentinel = contract. |
| `entityOwner(bytes32) / entityPrice(bytes32)` | same | as above, keyed by keccak of the entity id |
| `arcOwner(bytes32) / arcPrice(bytes32)` | same | as above, keyed by keccak of the arc id |

---

## Events

Text content lives in events. The API indexes events; the contract stores only structural
metadata.

### Story

- `LinkAdded(uint256 linkId, uint256 parentId, address author, string text)`
- `RecapAdded(uint256 linkId, uint256 parentId, address author, uint256 coversFromId, uint256 coversToId, string text)`
- `EntityCreated(bytes32 entityKey, string entityId, string name, string entityType, string description, address creator)`
- `ArcCreated(bytes32 arcKey, string arcId, uint256 anchorLinkId, string description, address creator)`
- `LinkVoted(uint256 linkId, address voter)`
- `LinkCleared(uint256 linkId)`, moderation; API blanks text
- `EntityCleared(bytes32 entityKey, string entityId)`, same, for entities
- `ArcCleared(bytes32 arcKey, string arcId)`, same, for arcs
- `CitizenRegistered(address citizen, string name)`
- `CitizenRenamed(address citizen, string name)`
- `CitizenBanned(address citizen)`, `CitizenUnbanned(address citizen)`

### Marketplace

- `Listed(uint8 kind, bytes32 id, address owner, uint256 price)`
- `Unlisted(uint8 kind, bytes32 id, address owner)`
- `Sold(uint8 kind, bytes32 id, address seller, address buyer, uint256 price, bool firstSale, uint256 protocolCut, uint256 sellerCut)`
- `Withdrawn(address recipient, uint256 amount)`
- `ProtocolWithdrawn(address treasury, uint256 amount)`
- `TreasuryChanged(address newTreasury)`
- `FirstSalePriceChanged(uint256 newPrice)`

### Admin fee settings

- `RegistrationFeeUpdated(uint256 fee)`
- `ProtocolFeeUpdated(uint256 fee)`
- `WritingEnabledUpdated(bool enabled)`

---

## Errors

### Story

- `NotCitizen()`, caller is not registered
- `AlreadyRegistered()`, `register` called twice by the same address
- `Banned(address)`, banned addresses can't write or vote
- `NotBanned(address)`, `unbanCitizen` called on a non-banned address
- `WritingDisabled()`, `writingEnabled` is false
- `NotGenesisAuthor()`, only the owner may author link 0
- `NameEmpty()`, `NameTooLong(max, actual)`, citizen name (64 byte cap)
- `TextEmpty()`, `TextTooLong(max, actual)`, link text (1000 byte cap)
- `LinkDoesNotExist(linkId)`, referenced link missing
- `AlreadyVoted()`, one vote per citizen per link
- `InvalidRecapRange(from, to)`, `coversFromId > coversToId`
- `EntityIdEmpty`, `EntityIdTooLong`, `EntityNameEmpty`, `EntityNameTooLong`,
  `EntityDescriptionTooLong`, `EntityAlreadyExists(id)`, `EntityDoesNotExist(id)`,
  `InvalidEntityType(type)`, entity validation
- `ArcIdEmpty`, `ArcIdTooLong`, `ArcDescriptionEmpty`, `ArcDescriptionTooLong`,
  `ArcAlreadyExists(id)`, `ArcDoesNotExist(id)`, arc validation
- `InsufficientPayment(required, sent)`, fee payment underflow
- `TransferFailed()`, refund couldn't be delivered (very rare)

### Marketplace

- `AssetDoesNotExist()`, unknown `(kind, id)`
- `NotAssetOwner()`, caller is not the current owner (or contract still owns it)
- `InvalidPrice()`, listing with `price == 0`
- `NotForSale()`, buy attempted against an unlisted asset
- `CannotBuyOwnAsset()`, buyer is the current owner
- `PriceMismatch(onchainPrice, expectedPrice)`, frontrun guard
- `IncorrectPayment(required, sent)`, `msg.value != price`
- `NothingToWithdraw()`, `withdraw()` with no pending balance
- `ZeroAddress()`, `setTreasury(0x0)`

---

## Size limits (bytes, UTF-8)

| Field | Max |
|---|---|
| Link / recap text | 1000 |
| Citizen name | 64 |
| Entity id | 64 |
| Entity name | 128 |
| Entity description | 500 |
| Arc id | 64 |
| Arc description | 500 |

Also exposed as gas-free view functions: `maxLinkBytes()`, `maxNameBytes()`,
`maxEntityIdBytes()`, `maxEntityNameBytes()`, `maxEntityDescriptionBytes()`,
`maxArcIdBytes()`, `maxArcDescriptionBytes()`.

---

## Fees (initial deployment)

- Registration: `0.05 ETH`, settable by owner (`setRegistrationFee`)
- Protocol fee per write: `0 ETH`, settable by owner (`setProtocolFee`)
- First sale price: `0.0025 ETH`, settable by owner (`setFirstSalePrice`)
- Vote: gas only

---

## Admin functions (`onlyOwner`)

### Moderation
- `banCitizen(address)` / `unbanCitizen(address)`
- `clearLink(uint256)`, emits `LinkCleared`; API blanks text, link remains in the tree
- `clearEntity(string entityId)`, emits `EntityCleared`; API blanks description
- `clearArc(string arcId)`, emits `ArcCleared`; same

### Protocol controls
- `setWritingEnabled(bool)`, global kill switch for writes (voting and marketplace unaffected)
- `setRegistrationFee(uint256)`
- `setProtocolFee(uint256)`
- `setFirstSalePrice(uint256)`
- `setTreasury(address)`, destination for `withdrawProtocol`
- `withdrawProtocol()`, sweep `protocolBalance` to `treasury`

### Ownership (Solady `Ownable`)
- `owner()`, current admin address
- `transferOwnership(address)`, `renounceOwnership()`
- `requestOwnershipHandover()` / `cancelOwnershipHandover()` / `completeOwnershipHandover(address)`, two-step transfer

---

## Accounting invariant

At all times:

```
address(this).balance == protocolBalance + Σ pendingWithdrawals[*]
```

Every fee paid in (registration, protocol, sale protocol cut) increments `protocolBalance`.
Every seller/creator credit increments a `pendingWithdrawals` entry. Nothing else accumulates
ETH in the contract. Admin `withdrawProtocol` and user `withdraw` only touch their respective
ledgers; neither can drain the other.
