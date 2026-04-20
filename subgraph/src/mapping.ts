import {
  CitizenRegistered,
  CitizenRenamed,
  CitizenBanned,
  CitizenUnbanned,
  LinkCollected,
  EntityCollected,
  ArcCollected,
  Listed as ListedEvent,
  Unlisted as UnlistedEvent,
  Sold as SoldEvent,
  LinkCleared as LinkClearedEvent,
  EntityCleared as EntityClearedEvent,
  ArcCleared as ArcClearedEvent,
  FirstSalePriceChanged as FirstSalePriceChangedEvent,
  OperatorChanged as OperatorChangedEvent,
} from "../generated/Sprawl/Sprawl";

import {
  Citizen,
  CollectedAsset,
  AssetKeyLookup,
  Sale,
  Listing,
  ProtocolStats,
} from "../generated/schema";

import { BigInt, Bytes, log } from "@graphprotocol/graph-ts";

const STATS_ID = "global";

// AssetKind enum discriminator matching the contract.
const KIND_LINK: i32   = 0;
const KIND_ENTITY: i32 = 1;
const KIND_ARC: i32    = 2;


function getOrCreateStats(): ProtocolStats {
  let s = ProtocolStats.load(STATS_ID);
  if (s == null) {
    s = new ProtocolStats(STATS_ID);
    s.totalCitizens = 0;
    s.totalBanned = 0;
    s.totalCollectedLinks = 0;
    s.totalCollectedEntities = 0;
    s.totalCollectedArcs = 0;
    s.totalSales = 0;
    s.totalVolume = BigInt.zero();
    s.currentFirstSalePrice = BigInt.zero();
    s.currentOperator = Bytes.fromHexString("0x0000000000000000000000000000000000000000");
  }
  return s;
}


function getOrCreateCitizen(address: Bytes, block: BigInt, timestamp: BigInt): Citizen {
  const id = address.toHexString();
  let c = Citizen.load(id);
  if (c == null) {
    c = new Citizen(id);
    c.name = "";
    c.registeredAt = timestamp;
    c.registeredAtBlock = block;
    c.isBanned = false;
    c.totalCollectedAsCreator = 0;
    c.totalPurchases = 0;
    c.totalSales = 0;
  }
  return c;
}


// =============================================================================
// Citizen registry
// =============================================================================


export function handleCitizenRegistered(event: CitizenRegistered): void {
  const c = getOrCreateCitizen(event.params.citizen, event.block.number, event.block.timestamp);
  c.name = event.params.name;
  c.registeredAt = event.block.timestamp;
  c.registeredAtBlock = event.block.number;
  c.save();

  const stats = getOrCreateStats();
  stats.totalCitizens = stats.totalCitizens + 1;
  stats.save();
}


export function handleCitizenRenamed(event: CitizenRenamed): void {
  const c = getOrCreateCitizen(event.params.citizen, event.block.number, event.block.timestamp);
  c.name = event.params.name;
  c.save();
}


export function handleCitizenBanned(event: CitizenBanned): void {
  const c = getOrCreateCitizen(event.params.citizen, event.block.number, event.block.timestamp);
  c.isBanned = true;
  c.save();

  const stats = getOrCreateStats();
  stats.totalBanned = stats.totalBanned + 1;
  stats.save();
}


export function handleCitizenUnbanned(event: CitizenUnbanned): void {
  const c = getOrCreateCitizen(event.params.citizen, event.block.number, event.block.timestamp);
  c.isBanned = false;
  c.save();

  const stats = getOrCreateStats();
  if (stats.totalBanned > 0) {
    stats.totalBanned = stats.totalBanned - 1;
  }
  stats.save();
}


// =============================================================================
// Collections
// =============================================================================


function createAsset(id: string, kind: string, nativeId: string,
                     creator: Citizen, owner: Bytes,
                     collectedAt: BigInt, price: BigInt): CollectedAsset {
  const a = new CollectedAsset(id);
  a.kind = kind;
  a.nativeId = nativeId;
  a.creator = creator.id;
  a.owner = owner;
  a.collectedAt = collectedAt;
  a.authoredAt = collectedAt;  // we don't have authoredAt in events; use collect time
  a.firstSalePrice = price;
  a.listingPrice = BigInt.zero();
  a.cleared = false;
  return a;
}


export function handleLinkCollected(event: LinkCollected): void {
  const linkIdStr = event.params.linkId.toString();
  const id = "link-" + linkIdStr;
  const creator = getOrCreateCitizen(event.params.creator, event.block.number, event.block.timestamp);
  creator.totalCollectedAsCreator = creator.totalCollectedAsCreator + 1;
  creator.save();

  const a = createAsset(id, "Link", linkIdStr, creator, event.params.collector,
                         event.block.timestamp, event.params.price);
  a.parentLinkId = event.params.parentId.toString();
  a.isRecap = event.params.isRecap;
  if (event.params.isRecap) {
    a.coversFromId = event.params.coversFromId.toString();
    a.coversToId = event.params.coversToId.toString();
  }
  a.save();

  // Lookup so Listed/Sold events (which have bytes32 keys) can find this asset.
  const keyBytes = Bytes.fromByteArray(Bytes.fromBigInt(event.params.linkId));
  const lookupKey = _padLeft32(keyBytes).toHexString();
  const lookup = new AssetKeyLookup(lookupKey);
  lookup.asset = id;
  lookup.save();

  const stats = getOrCreateStats();
  stats.totalCollectedLinks = stats.totalCollectedLinks + 1;
  stats.save();
}


export function handleEntityCollected(event: EntityCollected): void {
  const key = event.params.key.toHexString();
  const id = "entity-" + key;
  const creator = getOrCreateCitizen(event.params.creator, event.block.number, event.block.timestamp);
  creator.totalCollectedAsCreator = creator.totalCollectedAsCreator + 1;
  creator.save();

  const a = createAsset(id, "Entity", event.params.entityId, creator, event.params.collector,
                         event.block.timestamp, event.params.price);
  a.save();

  const lookup = new AssetKeyLookup(key);
  lookup.asset = id;
  lookup.save();

  const stats = getOrCreateStats();
  stats.totalCollectedEntities = stats.totalCollectedEntities + 1;
  stats.save();
}


export function handleArcCollected(event: ArcCollected): void {
  const key = event.params.key.toHexString();
  const id = "arc-" + key;
  const creator = getOrCreateCitizen(event.params.creator, event.block.number, event.block.timestamp);
  creator.totalCollectedAsCreator = creator.totalCollectedAsCreator + 1;
  creator.save();

  const a = createAsset(id, "Arc", event.params.arcId, creator, event.params.collector,
                         event.block.timestamp, event.params.price);
  a.anchorLinkId = event.params.anchorLinkId.toString();
  a.save();

  const lookup = new AssetKeyLookup(key);
  lookup.asset = id;
  lookup.save();

  const stats = getOrCreateStats();
  stats.totalCollectedArcs = stats.totalCollectedArcs + 1;
  stats.save();
}


// =============================================================================
// Marketplace
// =============================================================================


function resolveAsset(keyHex: string): string {
  const lookup = AssetKeyLookup.load(keyHex);
  return lookup == null ? "" : lookup.asset;
}


export function handleListed(event: ListedEvent): void {
  const keyHex = event.params.id.toHexString();
  const assetId = resolveAsset(keyHex);
  if (assetId == "") {
    log.warning("Listed: no asset found for key {}", [keyHex]);
    return;
  }
  let listing = Listing.load(assetId);
  if (listing == null) {
    listing = new Listing(assetId);
    listing.asset = assetId;
  }
  listing.price = event.params.price;
  listing.owner = event.params.owner;
  listing.updatedAt = event.block.timestamp;
  listing.save();

  const a = CollectedAsset.load(assetId);
  if (a != null) {
    a.listingPrice = event.params.price;
    a.save();
  }
}


export function handleUnlisted(event: UnlistedEvent): void {
  const keyHex = event.params.id.toHexString();
  const assetId = resolveAsset(keyHex);
  if (assetId == "") return;
  const listing = Listing.load(assetId);
  if (listing != null) {
    listing.price = BigInt.zero();
    listing.updatedAt = event.block.timestamp;
    listing.save();
  }
  const a = CollectedAsset.load(assetId);
  if (a != null) {
    a.listingPrice = BigInt.zero();
    a.save();
  }
}


export function handleSold(event: SoldEvent): void {
  const keyHex = event.params.id.toHexString();
  const assetId = resolveAsset(keyHex);
  if (assetId == "") {
    log.warning("Sold: no asset found for key {}", [keyHex]);
    return;
  }

  const a = CollectedAsset.load(assetId);
  if (a == null) return;

  // Update ownership + clear listing.
  a.owner = event.params.buyer;
  a.listingPrice = BigInt.zero();
  a.save();

  const listing = Listing.load(assetId);
  if (listing != null) {
    listing.price = BigInt.zero();
    listing.updatedAt = event.block.timestamp;
    listing.save();
  }

  // On first sale, seller in the event is the contract itself; credit the
  // creator for the "displaySeller" role.
  let displaySeller: Bytes = event.params.seller;
  if (event.params.firstSale) {
    const creator = Citizen.load(a.creator);
    if (creator != null) {
      displaySeller = Bytes.fromHexString(creator.id) as Bytes;
    }
  }

  const saleId = event.transaction.hash.toHexString() + "-" + event.logIndex.toString();
  const sale = new Sale(saleId);
  sale.asset = assetId;
  sale.seller = displaySeller;
  sale.buyer = event.params.buyer;
  sale.price = event.params.price;
  sale.firstSale = event.params.firstSale;
  sale.protocolCut = event.params.protocolCut;
  sale.sellerCut = event.params.sellerCut;
  sale.blockNumber = event.block.number;
  sale.timestamp = event.block.timestamp;
  sale.save();

  const buyer = getOrCreateCitizen(event.params.buyer, event.block.number, event.block.timestamp);
  buyer.totalPurchases = buyer.totalPurchases + 1;
  buyer.save();

  const seller = getOrCreateCitizen(displaySeller, event.block.number, event.block.timestamp);
  seller.totalSales = seller.totalSales + 1;
  seller.save();

  const stats = getOrCreateStats();
  stats.totalSales = stats.totalSales + 1;
  stats.totalVolume = stats.totalVolume.plus(event.params.price);
  stats.save();
}


// =============================================================================
// Clears
// =============================================================================


export function handleLinkCleared(event: LinkClearedEvent): void {
  const id = "link-" + event.params.linkId.toString();
  const a = CollectedAsset.load(id);
  if (a != null) { a.cleared = true; a.save(); }
}


export function handleEntityCleared(event: EntityClearedEvent): void {
  const id = "entity-" + event.params.key.toHexString();
  const a = CollectedAsset.load(id);
  if (a != null) { a.cleared = true; a.save(); }
}


export function handleArcCleared(event: ArcClearedEvent): void {
  const id = "arc-" + event.params.key.toHexString();
  const a = CollectedAsset.load(id);
  if (a != null) { a.cleared = true; a.save(); }
}


// =============================================================================
// Admin state
// =============================================================================


export function handleFirstSalePriceChanged(event: FirstSalePriceChangedEvent): void {
  const stats = getOrCreateStats();
  stats.currentFirstSalePrice = event.params.newPrice;
  stats.save();
}


export function handleOperatorChanged(event: OperatorChangedEvent): void {
  const stats = getOrCreateStats();
  stats.currentOperator = event.params.newOperator;
  stats.save();
}


// =============================================================================
// Helpers
// =============================================================================


/** Left-pad a byte array to 32 bytes. */
function _padLeft32(b: Bytes): Bytes {
  const out = new Uint8Array(32);
  const src = b;
  const offset = 32 - src.length;
  for (let i = 0; i < src.length; i++) {
    out[offset + i] = src[i];
  }
  return Bytes.fromUint8Array(out);
}
