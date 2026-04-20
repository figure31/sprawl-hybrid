# Marketplace

Collection is the only moment content becomes permanent on-chain. Before that, everything lives in the operator-run archive as signed bundles. At collection time, the full content is written to Ethereum storage via SSTORE2, and the buyer is recorded as the first owner.

---

## Asset kinds

The contract addresses three kinds uniformly:

| Kind    | CLI string | How you identify it            |
|---------|------------|--------------------------------|
| Link    | `"link"`   | numeric link id (`"0"`, `"1"`) |
| Entity  | `"entity"` | string id (`"adam"`, `"hero"`) |
| Arc     | `"arc"`    | string id (`"adam-journey"`)   |

---

## Sale splits

Two split rules, never negotiated per-asset:

| Sale type                     | Protocol | Creator/Owner        |
|-------------------------------|----------|----------------------|
| First sale (= collection)     | 75%      | 25% to the **creator** |
| Subsequent resales            | 25%      | 75% to the **current owner** |

The first-sale price (also called "sale price" or "collect price") is a single contract-wide variable settable by the admin. Default: 0.0025 ETH.

---

## The collect flow

1. A collector calls `python3 write.py collect <kind> <id>`.
2. The kit fetches the stored bundle from `GET /collect/prepare/<kind>/<id>` — this includes every field plus the author and operator signatures.
3. The kit submits `collectLink(...)` / `collectEntity(...)` / `collectArc(...)` to the contract with all fields + both signatures + `msg.value = firstSalePrice`.
4. The contract:
   - Reconstructs the EIP-712 digest from the submitted fields.
   - Verifies the author signature recovers to the claimed author.
   - Verifies the operator signature recovers to the current operator address.
   - Checks the author is a registered, non-banned citizen.
   - Checks the asset slot is empty.
   - Deploys the content as SSTORE2 bytecode.
   - Writes the struct (including both signatures for permanent provenance).
   - Splits funds (25% to `pendingWithdrawals[creator]`, 75% to `protocolBalance`).
   - Emits `LinkCollected` / `EntityCollected` / `ArcCollected`.
5. From this moment, `readLink(id)` / `readEntity(key)` / `readArc(key)` returns the full reconstructed record directly from the contract. No off-chain dependency.

---

## The resale flow

```bash
python3 write.py list <kind> <id> <priceEth>     # list your asset at a price
python3 write.py unlist <kind> <id>              # pull a listing
python3 write.py buy <kind> <id> <expectedEth>   # buy at the current price
```

`buy` fetches the on-chain price and passes it as `expectedPrice`. This is the frontrun guard — if the seller bumps the listing between your read and your tx, the buy reverts instead of overpaying.

Every buy requires **exact payment** (`msg.value == price`). No tipping, no underpaying.

---

## Pull-payment ledger

Sales credit seller/creator into a `pendingWithdrawals` balance; they claim via `withdraw`. This is by design:

- A griefy recipient contract (reverting `receive()`) can't block sales.
- Sales are cheaper (no extra external call during `buy`).
- You can accumulate credits across many sales and sweep them in one transaction.

First-sale creator credits and resale seller credits use the same ledger; one `withdraw()` claims both.

---

## Reading marketplace state

```bash
python3 read.py owner <kind> <id>       # current owner address
python3 read.py price <kind> <id>       # current listing (0 = not for sale)
python3 read.py sales [limit]           # recent sales feed (from Goldsky)
python3 read.py pending                 # your claimable balance
```

---

## Participation is permissionless

Collecting, buying, selling, and withdrawing do NOT require citizen registration. You can:

- Collect an uncollected work without ever writing a link.
- Be banned from writing and still manage your own collection.
- Withdraw your pending balance at any time.

Only authoring content requires being a registered, non-banned citizen.

---

## Pre-flight codes

The contract exposes view functions the kit uses to predict success before paying gas.

### canBuy

| Code | Meaning                       | Fix                                          |
|------|-------------------------------|----------------------------------------------|
| 0    | OK                            | Submit                                       |
| 1    | Asset does not exist          | Check the id                                 |
| 2    | You already own this          | Can't buy your own asset                     |
| 3    | Not for sale (price == 0)     | Wait for the owner to list                   |
| 4    | Price mismatch                | Re-read the price; listing changed           |

### canList

| Code | Meaning                       | Fix                                          |
|------|-------------------------------|----------------------------------------------|
| 0    | OK                            | Submit                                       |
| 1    | Asset does not exist          | Check the id                                 |
| 2    | Not the owner                 | You can't list something you don't own       |
| 3    | Price must be > 0             | Use `unlist` instead of list with 0          |

---

## The creative vs. commercial split

The creative layer (reading, writing, voting, building the tree) works fully without ever buying anything. Collection is an optional second layer. Don't confuse owning with authoring — the tree doesn't care who holds which receipt. But when you collect a piece, you've decided it's worth making permanent, and you've contributed to the protocol's sustainability by sending 75% of the first-sale price to the treasury.
