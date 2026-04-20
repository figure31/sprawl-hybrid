// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Ownable} from "solady/auth/Ownable.sol";
import {ReentrancyGuard} from "solady/utils/ReentrancyGuard.sol";
import {SafeTransferLib} from "solady/utils/SafeTransferLib.sol";

/**
 * @title Sprawl
 * @notice Permanent, branching story protocol on Ethereum mainnet.
 *
 * On-chain surface:
 *   - Citizen registry (register, rename, ban) with slashable stake.
 *   - Operator address: the only key whose co-signature makes content
 *     collectable. Prevents direct-to-contract injection of unauthorized
 *     content.
 *   - Collectible records for links, entities, arcs. Each carries the
 *     full content via SSTORE2 plus both the author's and operator's
 *     EIP-712 signatures over the original message.
 *   - Marketplace: list, buy, unlist, pull-payment withdrawals.
 *   - Admin: clears, treasury, pricing, operator rotation, pause.
 *
 * Off-chain surface (not this contract):
 *   - Writing drafts of links, entities, arcs, recaps.
 *   - Votes.
 *   - Reading the living tree.
 *
 * Collection is the only moment a piece of content becomes permanent.
 * Before that, content lives in an operator-run archive gated by EIP-712
 * signatures. At collect time, the full content plus both signatures
 * are submitted to this contract. The contract verifies both signatures
 * against the exact bytes submitted, stores the content via SSTORE2, and
 * records the buyer as owner. From that moment the collectible is
 * reconstructible by a single read function without trusting any off-chain
 * system.
 */
contract Sprawl is Ownable, ReentrancyGuard {

    // -----------------------------------------------------------------------
    // Constants
    // -----------------------------------------------------------------------

    uint256 internal constant MAX_LINK_BYTES = 1000;
    uint256 internal constant MAX_NAME_BYTES = 64;
    uint256 internal constant MAX_ENTITY_ID_BYTES = 64;
    uint256 internal constant MAX_ENTITY_NAME_BYTES = 128;
    uint256 internal constant MAX_ENTITY_DESCRIPTION_BYTES = 500;
    uint256 internal constant MAX_ARC_ID_BYTES = 64;
    uint256 internal constant MAX_ARC_DESCRIPTION_BYTES = 500;

    // Marketplace split constants (basis points out of 10,000).
    // First sale = collection: protocol bootstraps by taking 75%, the
    // creator receives 25%. Resales: 75% to the current owner, 25% to
    // the protocol.
    uint256 internal constant BPS_DENOM                = 10_000;
    uint256 internal constant FIRST_SALE_PROTOCOL_BPS  = 7_500;
    uint256 internal constant RESALE_PROTOCOL_BPS      = 2_500;

    // -----------------------------------------------------------------------
    // EIP-712 domain + typehashes
    // -----------------------------------------------------------------------

    bytes32 private constant EIP712_DOMAIN_TYPEHASH =
        keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");

    // keccak256("Link(uint256 linkId,uint256 parentId,uint64 authoredAt,uint64 nonce,uint64 beaconBlock,bool isRecap,uint256 coversFromId,uint256 coversToId,address author,string text)")
    bytes32 private constant LINK_TYPEHASH =
        keccak256("Link(uint256 linkId,uint256 parentId,uint64 authoredAt,uint64 nonce,uint64 beaconBlock,bool isRecap,uint256 coversFromId,uint256 coversToId,address author,string text)");

    // keccak256("Entity(string entityId,string name,string entityType,string description,uint64 authoredAt,uint64 nonce,uint64 beaconBlock,address author)")
    bytes32 private constant ENTITY_TYPEHASH =
        keccak256("Entity(string entityId,string name,string entityType,string description,uint64 authoredAt,uint64 nonce,uint64 beaconBlock,address author)");

    // keccak256("Arc(string arcId,uint256 anchorLinkId,string description,uint64 authoredAt,uint64 nonce,uint64 beaconBlock,address author)")
    bytes32 private constant ARC_TYPEHASH =
        keccak256("Arc(string arcId,uint256 anchorLinkId,string description,uint64 authoredAt,uint64 nonce,uint64 beaconBlock,address author)");

    bytes32 private immutable _CACHED_DOMAIN_SEPARATOR;
    uint256 private immutable _CACHED_CHAIN_ID;

    // -----------------------------------------------------------------------
    // Types
    // -----------------------------------------------------------------------

    enum AssetKind { Link, Entity, Arc }

    /// @dev Compact EIP-712 signature bundle.
    struct Sig {
        bytes32 r;
        bytes32 s;
        uint8   v;
    }

    struct CitizenInfo {
        string  name;
        bool    isRegistered;
        bool    isBanned;
        uint32  totalCollected;     // number of assets this citizen authored that got collected
        uint64  registeredAt;
    }

    struct CollectedLink {
        address owner;
        uint64  collectedAt;
        uint64  authoredAt;
        bool    isRecap;
        bool    cleared;
        address creator;
        address contentPointer;     // SSTORE2 bytecode address for `text`
        uint256 parentId;
        uint256 price;              // listing price in wei, 0 if unlisted
        uint256 coversFromId;       // recap-only (0 when not recap)
        uint256 coversToId;         // recap-only
        Sig     authorSig;
        Sig     operatorSig;
    }

    struct CollectedEntity {
        address owner;
        uint64  collectedAt;
        uint64  authoredAt;
        bool    cleared;
        address creator;
        address contentPointer;     // SSTORE2 bytecode for packed (name || 0x00 || type || 0x00 || description)
        uint256 price;
        bytes32 entityIdHash;       // keccak256(bytes(entityId))
        Sig     authorSig;
        Sig     operatorSig;
    }

    struct CollectedArc {
        address owner;
        uint64  collectedAt;
        uint64  authoredAt;
        bool    cleared;
        address creator;
        address contentPointer;     // SSTORE2 bytecode for `description`
        uint256 anchorLinkId;
        uint256 price;
        bytes32 arcIdHash;          // keccak256(bytes(arcId))
        Sig     authorSig;
        Sig     operatorSig;
    }

    // View structs returned by readLink/readEntity/readArc (see §2.6 of
    // MAINNET_PLAN.md).

    struct LinkView {
        uint256 linkId;
        address creator;
        address owner;
        uint256 collectedAt;
        uint256 authoredAt;
        uint256 parentId;
        bool    isRecap;
        bool    cleared;
        uint256 coversFromId;
        uint256 coversToId;
        bytes   text;
        bytes   authorSignature;    // abi.encodePacked(r, s, v)
        bytes   operatorSignature;
        uint256 price;
    }

    struct EntityView {
        bytes32 key;
        address creator;
        address owner;
        uint256 collectedAt;
        uint256 authoredAt;
        bool    cleared;
        bytes   content;            // name || 0x00 || type || 0x00 || description
        bytes   authorSignature;
        bytes   operatorSignature;
        uint256 price;
    }

    struct ArcView {
        bytes32 key;
        address creator;
        address owner;
        uint256 collectedAt;
        uint256 authoredAt;
        uint256 anchorLinkId;
        bool    cleared;
        bytes   description;
        bytes   authorSignature;
        bytes   operatorSignature;
        uint256 price;
    }

    // -----------------------------------------------------------------------
    // Storage
    // -----------------------------------------------------------------------

    mapping(address => CitizenInfo) public citizens;

    // The operator is the off-chain service whose co-signature every
    // collectible must carry. Only signatures recovering to this address
    // make collections valid.
    address public operator;

    mapping(uint256 => CollectedLink)   public collectedLinks;
    mapping(bytes32 => CollectedEntity) public collectedEntities;
    mapping(bytes32 => CollectedArc)    public collectedArcs;

    uint256 public registrationFee;
    uint256 public firstSalePrice;
    bool    public paused;

    // Admin accounting. `protocolBalance` tracks what the treasury is owed
    // (slashed stakes + first-sale and resale cuts). Kept separate from
    // `pendingWithdrawals` so the admin can never touch user credits.
    uint256 public protocolBalance;
    address public treasury;

    // Pull-payment ledger. Buyers never cause a push to sellers/creators
    // during buy(); funds are credited here and claimed via withdraw().
    mapping(address => uint256) public pendingWithdrawals;

    // -----------------------------------------------------------------------
    // Events
    // -----------------------------------------------------------------------

    event CitizenRegistered(address indexed citizen, string name);
    event CitizenRenamed(address indexed citizen, string name);
    event CitizenBanned(address indexed citizen);
    event CitizenUnbanned(address indexed citizen);

    event LinkCollected(
        uint256 indexed linkId,
        address indexed creator,
        address indexed collector,
        uint256 parentId,
        bool    isRecap,
        uint256 coversFromId,
        uint256 coversToId,
        uint256 price
    );
    event EntityCollected(
        bytes32 indexed key,
        string  entityId,
        address indexed creator,
        address indexed collector,
        uint256 price
    );
    event ArcCollected(
        bytes32 indexed key,
        string  arcId,
        uint256 indexed anchorLinkId,
        address creator,
        address collector,
        uint256 price
    );

    event Listed(AssetKind indexed kind, bytes32 indexed id, address indexed owner, uint256 price);
    event Unlisted(AssetKind indexed kind, bytes32 indexed id, address indexed owner);
    event Sold(
        AssetKind indexed kind,
        bytes32 indexed id,
        address indexed seller,
        address buyer,
        uint256 price,
        bool firstSale,
        uint256 protocolCut,
        uint256 sellerCut
    );

    event Withdrawn(address indexed recipient, uint256 amount);
    event ProtocolWithdrawn(address indexed treasury, uint256 amount);

    event LinkCleared(uint256 indexed linkId);
    event EntityCleared(bytes32 indexed key);
    event ArcCleared(bytes32 indexed key);

    event RegistrationFeeChanged(uint256 newFee);
    event FirstSalePriceChanged(uint256 newPrice);
    event TreasuryChanged(address indexed newTreasury);
    event OperatorChanged(address indexed oldOperator, address indexed newOperator);
    event PausedChanged(bool paused);

    // -----------------------------------------------------------------------
    // Errors
    // -----------------------------------------------------------------------

    error NotCitizen();
    error AlreadyRegistered();
    error Banned(address citizen);
    error NotBanned(address citizen);
    error Paused();

    error NameEmpty();
    error NameTooLong(uint256 maxBytes, uint256 actualBytes);

    error TextEmpty();
    error TextTooLong(uint256 maxBytes, uint256 actualBytes);

    error EntityIdEmpty();
    error EntityIdTooLong(uint256 maxBytes, uint256 actualBytes);
    error EntityNameEmpty();
    error EntityNameTooLong(uint256 maxBytes, uint256 actualBytes);
    error EntityDescriptionTooLong(uint256 maxBytes, uint256 actualBytes);
    error InvalidEntityType(string entityType);

    error ArcIdEmpty();
    error ArcIdTooLong(uint256 maxBytes, uint256 actualBytes);
    error ArcDescriptionEmpty();
    error ArcDescriptionTooLong(uint256 maxBytes, uint256 actualBytes);

    error AlreadyCollected();
    error AnchorLinkNotCollected();
    error BadAuthorSig();
    error BadOperatorSig();
    error InvalidRecapRange();

    error InsufficientPayment(uint256 required, uint256 sent);
    error IncorrectPayment(uint256 required, uint256 sent);

    error AssetDoesNotExist();
    error NotAssetOwner();
    error InvalidPrice();
    error NotForSale();
    error CannotBuyOwnAsset();
    error PriceMismatch(uint256 onchainPrice, uint256 expectedPrice);
    error NothingToWithdraw();
    error ZeroAddress();

    // -----------------------------------------------------------------------
    // Constructor
    // -----------------------------------------------------------------------

    constructor(
        uint256 _registrationFee,
        uint256 _firstSalePrice,
        address _operator
    ) {
        if (_operator == address(0)) revert ZeroAddress();

        _initializeOwner(msg.sender);
        registrationFee = _registrationFee;
        firstSalePrice  = _firstSalePrice;
        operator        = _operator;
        treasury        = msg.sender;

        _CACHED_CHAIN_ID = block.chainid;
        _CACHED_DOMAIN_SEPARATOR = _buildDomainSeparator();

        emit OperatorChanged(address(0), _operator);
    }

    // -----------------------------------------------------------------------
    // Citizen registry
    // -----------------------------------------------------------------------

    function register(string calldata name) external payable nonReentrant {
        if (paused) revert Paused();
        if (citizens[msg.sender].isRegistered) revert AlreadyRegistered();
        _validateName(name);

        uint256 fee = registrationFee;
        if (msg.value < fee) revert InsufficientPayment(fee, msg.value);

        citizens[msg.sender] = CitizenInfo({
            name:            name,
            isRegistered:    true,
            isBanned:        false,
            totalCollected:  0,
            registeredAt:    uint64(block.timestamp)
        });

        // Admin ledger: registration fee goes to protocol.
        protocolBalance += fee;

        emit CitizenRegistered(msg.sender, name);

        // Refund any overpayment.
        uint256 overpayment = msg.value - fee;
        if (overpayment > 0) SafeTransferLib.safeTransferETH(msg.sender, overpayment);
    }

    function renameCitizen(string calldata name) external {
        CitizenInfo storage c = citizens[msg.sender];
        if (!c.isRegistered) revert NotCitizen();
        if (c.isBanned) revert Banned(msg.sender);
        _validateName(name);
        c.name = name;
        emit CitizenRenamed(msg.sender, name);
    }

    // -----------------------------------------------------------------------
    // Collection: links
    // -----------------------------------------------------------------------

    /// @notice Collect a link. Verifies both author and operator signatures
    /// against the submitted fields, then writes the full content to
    /// chain storage via SSTORE2.
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
        bytes calldata text,
        Sig   calldata authorSig,
        Sig   calldata operatorSig
    ) external payable nonReentrant {
        if (paused) revert Paused();
        if (collectedLinks[linkId].contentPointer != address(0)) revert AlreadyCollected();

        _validateText(text);
        if (isRecap && coversFromId > coversToId) revert InvalidRecapRange();
        if (msg.value != firstSalePrice) revert IncorrectPayment(firstSalePrice, msg.value);

        // --- Reconstruct EIP-712 digest over exactly the submitted fields ---
        bytes32 structHash = keccak256(abi.encode(
            LINK_TYPEHASH,
            linkId,
            parentId,
            authoredAt,
            nonce,
            beaconBlock,
            isRecap,
            coversFromId,
            coversToId,
            author,
            keccak256(text)
        ));
        bytes32 digest = _hashTypedData(structHash);

        // --- Verify both signatures ---
        _verifyAuthor(digest, author, authorSig);
        _verifyOperator(digest, operatorSig);

        // --- Citizen checks ---
        CitizenInfo storage c = citizens[author];
        if (!c.isRegistered) revert NotCitizen();
        if (c.isBanned) revert Banned(author);

        // --- Effects ---
        address ptr = _sstore2Write(text);
        collectedLinks[linkId] = CollectedLink({
            owner:           msg.sender,
            collectedAt:     uint64(block.timestamp),
            authoredAt:      authoredAt,
            isRecap:         isRecap,
            cleared:         false,
            creator:         author,
            contentPointer:  ptr,
            parentId:        parentId,
            price:           0,
            coversFromId:    isRecap ? coversFromId : 0,
            coversToId:      isRecap ? coversToId   : 0,
            authorSig:       authorSig,
            operatorSig:     operatorSig
        });

        c.totalCollected += 1;
        _splitFirstSale(author, msg.value);

        emit LinkCollected(linkId, author, msg.sender, parentId, isRecap, coversFromId, coversToId, msg.value);
        emit Sold(
            AssetKind.Link,
            bytes32(linkId),
            address(this),
            msg.sender,
            msg.value,
            true,
            (msg.value * FIRST_SALE_PROTOCOL_BPS) / BPS_DENOM,
            msg.value - (msg.value * FIRST_SALE_PROTOCOL_BPS) / BPS_DENOM
        );
    }

    // -----------------------------------------------------------------------
    // Collection: entities
    // -----------------------------------------------------------------------

    function collectEntity(
        string calldata entityId,
        string calldata name,
        string calldata entityType,
        string calldata description,
        uint64  authoredAt,
        uint64  nonce,
        uint64  beaconBlock,
        address author,
        Sig   calldata authorSig,
        Sig   calldata operatorSig
    ) external payable nonReentrant {
        if (paused) revert Paused();

        _validateEntityId(entityId);
        _validateEntityName(name);
        _validateEntityType(entityType);
        _validateEntityDescription(description);

        bytes32 key = keccak256(bytes(entityId));
        if (collectedEntities[key].contentPointer != address(0)) revert AlreadyCollected();
        if (msg.value != firstSalePrice) revert IncorrectPayment(firstSalePrice, msg.value);

        bytes32 structHash = keccak256(abi.encode(
            ENTITY_TYPEHASH,
            keccak256(bytes(entityId)),
            keccak256(bytes(name)),
            keccak256(bytes(entityType)),
            keccak256(bytes(description)),
            authoredAt,
            nonce,
            beaconBlock,
            author
        ));
        bytes32 digest = _hashTypedData(structHash);

        _verifyAuthor(digest, author, authorSig);
        _verifyOperator(digest, operatorSig);

        CitizenInfo storage c = citizens[author];
        if (!c.isRegistered) revert NotCitizen();
        if (c.isBanned) revert Banned(author);

        bytes memory packed = _packEntityContent(name, entityType, description);
        address ptr = _sstore2Write(packed);

        collectedEntities[key] = CollectedEntity({
            owner:           msg.sender,
            collectedAt:     uint64(block.timestamp),
            authoredAt:      authoredAt,
            cleared:         false,
            creator:         author,
            contentPointer:  ptr,
            price:           0,
            entityIdHash:    key,
            authorSig:       authorSig,
            operatorSig:     operatorSig
        });

        c.totalCollected += 1;
        _splitFirstSale(author, msg.value);

        emit EntityCollected(key, entityId, author, msg.sender, msg.value);
        emit Sold(
            AssetKind.Entity,
            key,
            address(this),
            msg.sender,
            msg.value,
            true,
            (msg.value * FIRST_SALE_PROTOCOL_BPS) / BPS_DENOM,
            msg.value - (msg.value * FIRST_SALE_PROTOCOL_BPS) / BPS_DENOM
        );
    }

    // -----------------------------------------------------------------------
    // Collection: arcs
    // -----------------------------------------------------------------------

    function collectArc(
        string calldata arcId,
        uint256 anchorLinkId,
        string  calldata description,
        uint64  authoredAt,
        uint64  nonce,
        uint64  beaconBlock,
        address author,
        Sig   calldata authorSig,
        Sig   calldata operatorSig
    ) external payable nonReentrant {
        if (paused) revert Paused();

        _validateArcId(arcId);
        _validateArcDescription(description);

        bytes32 key = keccak256(bytes(arcId));
        if (collectedArcs[key].contentPointer != address(0)) revert AlreadyCollected();
        if (msg.value != firstSalePrice) revert IncorrectPayment(firstSalePrice, msg.value);

        // Anchor link must already be collected on-chain. Arcs reference
        // a specific point in the tree; that point must exist permanently
        // for the arc to make sense on-chain.
        if (collectedLinks[anchorLinkId].contentPointer == address(0)) revert AnchorLinkNotCollected();

        bytes32 structHash = keccak256(abi.encode(
            ARC_TYPEHASH,
            keccak256(bytes(arcId)),
            anchorLinkId,
            keccak256(bytes(description)),
            authoredAt,
            nonce,
            beaconBlock,
            author
        ));
        bytes32 digest = _hashTypedData(structHash);

        _verifyAuthor(digest, author, authorSig);
        _verifyOperator(digest, operatorSig);

        CitizenInfo storage c = citizens[author];
        if (!c.isRegistered) revert NotCitizen();
        if (c.isBanned) revert Banned(author);

        address ptr = _sstore2Write(bytes(description));

        collectedArcs[key] = CollectedArc({
            owner:           msg.sender,
            collectedAt:     uint64(block.timestamp),
            authoredAt:      authoredAt,
            cleared:         false,
            creator:         author,
            contentPointer:  ptr,
            anchorLinkId:    anchorLinkId,
            price:           0,
            arcIdHash:       key,
            authorSig:       authorSig,
            operatorSig:     operatorSig
        });

        c.totalCollected += 1;
        _splitFirstSale(author, msg.value);

        emit ArcCollected(key, arcId, anchorLinkId, author, msg.sender, msg.value);
        emit Sold(
            AssetKind.Arc,
            key,
            address(this),
            msg.sender,
            msg.value,
            true,
            (msg.value * FIRST_SALE_PROTOCOL_BPS) / BPS_DENOM,
            msg.value - (msg.value * FIRST_SALE_PROTOCOL_BPS) / BPS_DENOM
        );
    }

    // -----------------------------------------------------------------------
    // Read / reconstruction
    // -----------------------------------------------------------------------

    function readLink(uint256 linkId) external view returns (LinkView memory v) {
        CollectedLink storage c = collectedLinks[linkId];
        if (c.contentPointer == address(0)) revert AssetDoesNotExist();

        v.linkId           = linkId;
        v.creator          = c.creator;
        v.owner            = c.owner;
        v.collectedAt      = c.collectedAt;
        v.authoredAt       = c.authoredAt;
        v.parentId         = c.parentId;
        v.isRecap          = c.isRecap;
        v.cleared          = c.cleared;
        v.coversFromId     = c.coversFromId;
        v.coversToId       = c.coversToId;
        v.text             = _sstore2Read(c.contentPointer);
        v.authorSignature  = abi.encodePacked(c.authorSig.r, c.authorSig.s, c.authorSig.v);
        v.operatorSignature= abi.encodePacked(c.operatorSig.r, c.operatorSig.s, c.operatorSig.v);
        v.price            = c.price;
    }

    function readEntity(bytes32 key) external view returns (EntityView memory v) {
        CollectedEntity storage c = collectedEntities[key];
        if (c.contentPointer == address(0)) revert AssetDoesNotExist();

        v.key              = key;
        v.creator          = c.creator;
        v.owner            = c.owner;
        v.collectedAt      = c.collectedAt;
        v.authoredAt       = c.authoredAt;
        v.cleared          = c.cleared;
        v.content          = _sstore2Read(c.contentPointer);
        v.authorSignature  = abi.encodePacked(c.authorSig.r, c.authorSig.s, c.authorSig.v);
        v.operatorSignature= abi.encodePacked(c.operatorSig.r, c.operatorSig.s, c.operatorSig.v);
        v.price            = c.price;
    }

    function readArc(bytes32 key) external view returns (ArcView memory v) {
        CollectedArc storage c = collectedArcs[key];
        if (c.contentPointer == address(0)) revert AssetDoesNotExist();

        v.key              = key;
        v.creator          = c.creator;
        v.owner            = c.owner;
        v.collectedAt      = c.collectedAt;
        v.authoredAt       = c.authoredAt;
        v.anchorLinkId     = c.anchorLinkId;
        v.cleared          = c.cleared;
        v.description      = _sstore2Read(c.contentPointer);
        v.authorSignature  = abi.encodePacked(c.authorSig.r, c.authorSig.s, c.authorSig.v);
        v.operatorSignature= abi.encodePacked(c.operatorSig.r, c.operatorSig.s, c.operatorSig.v);
        v.price            = c.price;
    }

    // -----------------------------------------------------------------------
    // Marketplace (resale on already-collected assets)
    // -----------------------------------------------------------------------

    function list(AssetKind kind, bytes32 id, uint256 price) external {
        if (!_assetExists(kind, id)) revert AssetDoesNotExist();
        if (price == 0) revert InvalidPrice();
        address owner_ = _ownerOf(kind, id);
        if (msg.sender != owner_) revert NotAssetOwner();
        _setPrice(kind, id, price);
        emit Listed(kind, id, msg.sender, price);
    }

    function unlist(AssetKind kind, bytes32 id) external {
        if (!_assetExists(kind, id)) revert AssetDoesNotExist();
        address owner_ = _ownerOf(kind, id);
        if (msg.sender != owner_) revert NotAssetOwner();
        _setPrice(kind, id, 0);
        emit Unlisted(kind, id, msg.sender);
    }

    function buy(AssetKind kind, bytes32 id, uint256 expectedPrice) external payable nonReentrant {
        if (!_assetExists(kind, id)) revert AssetDoesNotExist();
        address seller = _ownerOf(kind, id);
        if (seller == msg.sender) revert CannotBuyOwnAsset();

        uint256 price = _priceOf(kind, id);
        if (price == 0) revert NotForSale();
        if (price != expectedPrice) revert PriceMismatch(price, expectedPrice);
        if (msg.value != price) revert IncorrectPayment(price, msg.value);

        uint256 protocolCut = (price * RESALE_PROTOCOL_BPS) / BPS_DENOM;
        uint256 sellerCut   = price - protocolCut;

        // --- Effects ---
        _setOwner(kind, id, msg.sender);
        _setPrice(kind, id, 0);
        protocolBalance += protocolCut;
        pendingWithdrawals[seller] += sellerCut;

        emit Sold(kind, id, seller, msg.sender, price, false, protocolCut, sellerCut);
    }

    /// @notice Claim ETH credited to msg.sender via pendingWithdrawals.
    function withdraw() external nonReentrant {
        uint256 amount = pendingWithdrawals[msg.sender];
        if (amount == 0) revert NothingToWithdraw();
        pendingWithdrawals[msg.sender] = 0;
        SafeTransferLib.safeTransferETH(msg.sender, amount);
        emit Withdrawn(msg.sender, amount);
    }

    // -----------------------------------------------------------------------
    // Pre-flight checks (view)
    // -----------------------------------------------------------------------

    /// @dev canList status codes: 0=ok 1=not-exist 2=not-owner 3=zero-price
    function canList(address seller, AssetKind kind, bytes32 id, uint256 price)
        external
        view
        returns (uint8)
    {
        if (!_assetExists(kind, id)) return 1;
        if (_ownerOf(kind, id) != seller) return 2;
        if (price == 0) return 3;
        return 0;
    }

    /// @dev canBuy status codes: 0=ok 1=not-exist 2=self-buy 3=not-for-sale 4=price-mismatch
    function canBuy(address buyer, AssetKind kind, bytes32 id, uint256 expectedPrice)
        external
        view
        returns (uint8)
    {
        if (!_assetExists(kind, id)) return 1;
        if (_ownerOf(kind, id) == buyer) return 2;
        uint256 price = _priceOf(kind, id);
        if (price == 0) return 3;
        if (price != expectedPrice) return 4;
        return 0;
    }

    function ownerOf(AssetKind kind, bytes32 id) external view returns (address) {
        if (!_assetExists(kind, id)) revert AssetDoesNotExist();
        return _ownerOf(kind, id);
    }

    function priceOf(AssetKind kind, bytes32 id) external view returns (uint256) {
        if (!_assetExists(kind, id)) revert AssetDoesNotExist();
        return _priceOf(kind, id);
    }

    // -----------------------------------------------------------------------
    // Admin
    // -----------------------------------------------------------------------

    function setRegistrationFee(uint256 fee) external onlyOwner {
        registrationFee = fee;
        emit RegistrationFeeChanged(fee);
    }

    function setFirstSalePrice(uint256 price) external onlyOwner {
        firstSalePrice = price;
        emit FirstSalePriceChanged(price);
    }

    function setTreasury(address newTreasury) external onlyOwner {
        if (newTreasury == address(0)) revert ZeroAddress();
        treasury = newTreasury;
        emit TreasuryChanged(newTreasury);
    }

    function setOperator(address newOperator) external onlyOwner {
        if (newOperator == address(0)) revert ZeroAddress();
        address old = operator;
        operator = newOperator;
        emit OperatorChanged(old, newOperator);
    }

    function setPaused(bool p) external onlyOwner {
        paused = p;
        emit PausedChanged(p);
    }

    function banCitizen(address citizen) external onlyOwner {
        CitizenInfo storage c = citizens[citizen];
        if (!c.isRegistered) revert NotCitizen();
        if (c.isBanned) revert Banned(citizen);
        c.isBanned = true;
        emit CitizenBanned(citizen);
    }

    function unbanCitizen(address citizen) external onlyOwner {
        CitizenInfo storage c = citizens[citizen];
        if (!c.isBanned) revert NotBanned(citizen);
        c.isBanned = false;
        emit CitizenUnbanned(citizen);
    }

    function clearLink(uint256 linkId) external onlyOwner {
        CollectedLink storage c = collectedLinks[linkId];
        if (c.contentPointer == address(0)) revert AssetDoesNotExist();
        c.cleared = true;
        emit LinkCleared(linkId);
    }

    function clearEntity(bytes32 key) external onlyOwner {
        CollectedEntity storage c = collectedEntities[key];
        if (c.contentPointer == address(0)) revert AssetDoesNotExist();
        c.cleared = true;
        emit EntityCleared(key);
    }

    function clearArc(bytes32 key) external onlyOwner {
        CollectedArc storage c = collectedArcs[key];
        if (c.contentPointer == address(0)) revert AssetDoesNotExist();
        c.cleared = true;
        emit ArcCleared(key);
    }

    /// @notice Sweep accumulated protocol balance to the current treasury.
    function withdrawProtocol() external onlyOwner nonReentrant {
        uint256 amount = protocolBalance;
        if (amount == 0) revert NothingToWithdraw();
        protocolBalance = 0;
        SafeTransferLib.safeTransferETH(treasury, amount);
        emit ProtocolWithdrawn(treasury, amount);
    }

    // -----------------------------------------------------------------------
    // EIP-712 helpers
    // -----------------------------------------------------------------------

    function DOMAIN_SEPARATOR() external view returns (bytes32) {
        return _domainSeparator();
    }

    function _domainSeparator() internal view returns (bytes32) {
        // Re-derive if chainId changed (fork safety).
        if (block.chainid == _CACHED_CHAIN_ID) return _CACHED_DOMAIN_SEPARATOR;
        return _buildDomainSeparator();
    }

    function _buildDomainSeparator() internal view returns (bytes32) {
        return keccak256(abi.encode(
            EIP712_DOMAIN_TYPEHASH,
            keccak256(bytes("Sprawl")),
            keccak256(bytes("1")),
            block.chainid,
            address(this)
        ));
    }

    function _hashTypedData(bytes32 structHash) internal view returns (bytes32) {
        return keccak256(abi.encodePacked(
            bytes2(0x1901),
            _domainSeparator(),
            structHash
        ));
    }

    function _verifyAuthor(bytes32 digest, address expected, Sig calldata s) internal pure {
        address recovered = ecrecover(digest, s.v, s.r, s.s);
        if (recovered == address(0) || recovered != expected) revert BadAuthorSig();
    }

    function _verifyOperator(bytes32 digest, Sig calldata s) internal view {
        address recovered = ecrecover(digest, s.v, s.r, s.s);
        if (recovered == address(0) || recovered != operator) revert BadOperatorSig();
    }

    // -----------------------------------------------------------------------
    // Marketplace helpers
    // -----------------------------------------------------------------------

    function _assetExists(AssetKind kind, bytes32 id) internal view returns (bool) {
        if (kind == AssetKind.Link)   return collectedLinks[uint256(id)].contentPointer != address(0);
        if (kind == AssetKind.Entity) return collectedEntities[id].contentPointer != address(0);
        return collectedArcs[id].contentPointer != address(0);
    }

    function _ownerOf(AssetKind kind, bytes32 id) internal view returns (address) {
        if (kind == AssetKind.Link)   return collectedLinks[uint256(id)].owner;
        if (kind == AssetKind.Entity) return collectedEntities[id].owner;
        return collectedArcs[id].owner;
    }

    function _priceOf(AssetKind kind, bytes32 id) internal view returns (uint256) {
        if (kind == AssetKind.Link)   return collectedLinks[uint256(id)].price;
        if (kind == AssetKind.Entity) return collectedEntities[id].price;
        return collectedArcs[id].price;
    }

    function _setOwner(AssetKind kind, bytes32 id, address newOwner) internal {
        if (kind == AssetKind.Link)        collectedLinks[uint256(id)].owner = newOwner;
        else if (kind == AssetKind.Entity) collectedEntities[id].owner = newOwner;
        else                               collectedArcs[id].owner = newOwner;
    }

    function _setPrice(AssetKind kind, bytes32 id, uint256 price) internal {
        if (kind == AssetKind.Link)        collectedLinks[uint256(id)].price = price;
        else if (kind == AssetKind.Entity) collectedEntities[id].price = price;
        else                               collectedArcs[id].price = price;
    }

    function _splitFirstSale(address creator, uint256 amount) internal {
        uint256 protocolCut = (amount * FIRST_SALE_PROTOCOL_BPS) / BPS_DENOM;
        uint256 creatorCut  = amount - protocolCut;
        protocolBalance += protocolCut;
        pendingWithdrawals[creator] += creatorCut;
    }

    // -----------------------------------------------------------------------
    // SSTORE2 helpers (inlined; see MAINNET_PLAN.md §2.3)
    // -----------------------------------------------------------------------

    function _sstore2Write(bytes memory data) internal returns (address pointer) {
        // Standard SSTORE2 init code. Deploys a contract whose bytecode is
        // [0x00 STOP byte] || data. Readers use EXTCODECOPY starting at
        // offset 1 to skip the STOP prefix and retrieve the original data.
        //
        // Byte layout of init code:
        //   0x00-0x02: 61 LEN LEN      PUSH2 (1 + data.length)
        //   0x03     : 80              DUP1
        //   0x04-0x05: 60 0C           PUSH1 0x0C (offset where runtime begins)
        //   0x06-0x07: 60 00           PUSH1 0x00 (dest memory offset)
        //   0x08     : 39              CODECOPY
        //   0x09-0x0A: 60 00           PUSH1 0x00 (return memory offset)
        //   0x0B     : F3              RETURN
        //   0x0C     : 00              STOP prefix (start of runtime code)
        //   0x0D+    : data
        bytes memory init = abi.encodePacked(
            hex"61",                        // PUSH2
            uint16(data.length + 1),        // runtime length = STOP + data
            hex"80",                        // DUP1
            hex"600c",                      // PUSH1 0x0C (offset of runtime in init code)
            hex"6000",                      // PUSH1 0x00 (dest memory)
            hex"39",                        // CODECOPY
            hex"6000",                      // PUSH1 0x00 (return from memory[0])
            hex"f3",                        // RETURN
            hex"00",                        // STOP byte (first byte of runtime)
            data
        );
        assembly {
            pointer := create(0, add(init, 0x20), mload(init))
        }
        if (pointer == address(0)) revert SSTORE2Failed();
    }

    error SSTORE2Failed();

    function _sstore2Read(address pointer) internal view returns (bytes memory out) {
        uint256 size;
        assembly { size := extcodesize(pointer) }
        if (size <= 1) return new bytes(0);
        uint256 len = size - 1;
        out = new bytes(len);
        assembly {
            extcodecopy(pointer, add(out, 0x20), 1, len)
        }
    }

    function _packEntityContent(
        string calldata name,
        string calldata entityType,
        string calldata description
    ) internal pure returns (bytes memory) {
        // Format: name || 0x00 || type || 0x00 || description.
        // Readers split on 0x00 delimiter.
        return abi.encodePacked(name, bytes1(0x00), entityType, bytes1(0x00), description);
    }

    // -----------------------------------------------------------------------
    // Validation
    // -----------------------------------------------------------------------

    function _validateName(string calldata name) internal pure {
        uint256 len = bytes(name).length;
        if (len == 0) revert NameEmpty();
        if (len > MAX_NAME_BYTES) revert NameTooLong(MAX_NAME_BYTES, len);
    }

    function _validateText(bytes calldata text) internal pure {
        uint256 len = text.length;
        if (len == 0) revert TextEmpty();
        if (len > MAX_LINK_BYTES) revert TextTooLong(MAX_LINK_BYTES, len);
    }

    function _validateEntityId(string calldata entityId) internal pure {
        uint256 len = bytes(entityId).length;
        if (len == 0) revert EntityIdEmpty();
        if (len > MAX_ENTITY_ID_BYTES) revert EntityIdTooLong(MAX_ENTITY_ID_BYTES, len);
    }

    function _validateEntityName(string calldata name) internal pure {
        uint256 len = bytes(name).length;
        if (len == 0) revert EntityNameEmpty();
        if (len > MAX_ENTITY_NAME_BYTES) revert EntityNameTooLong(MAX_ENTITY_NAME_BYTES, len);
    }

    function _validateEntityDescription(string calldata desc) internal pure {
        uint256 len = bytes(desc).length;
        if (len > MAX_ENTITY_DESCRIPTION_BYTES) revert EntityDescriptionTooLong(MAX_ENTITY_DESCRIPTION_BYTES, len);
    }

    function _validateEntityType(string calldata entityType) internal pure {
        bytes32 h = keccak256(bytes(entityType));
        if (
            h != keccak256("character") &&
            h != keccak256("place") &&
            h != keccak256("object") &&
            h != keccak256("event")
        ) revert InvalidEntityType(entityType);
    }

    function _validateArcId(string calldata arcId) internal pure {
        uint256 len = bytes(arcId).length;
        if (len == 0) revert ArcIdEmpty();
        if (len > MAX_ARC_ID_BYTES) revert ArcIdTooLong(MAX_ARC_ID_BYTES, len);
    }

    function _validateArcDescription(string calldata desc) internal pure {
        uint256 len = bytes(desc).length;
        if (len == 0) revert ArcDescriptionEmpty();
        if (len > MAX_ARC_DESCRIPTION_BYTES) revert ArcDescriptionTooLong(MAX_ARC_DESCRIPTION_BYTES, len);
    }

    // -----------------------------------------------------------------------
    // Public constants as views (gas-free reads for integrators)
    // -----------------------------------------------------------------------

    function maxLinkBytes() external pure returns (uint256) { return MAX_LINK_BYTES; }
    function maxNameBytes() external pure returns (uint256) { return MAX_NAME_BYTES; }
    function maxEntityIdBytes() external pure returns (uint256) { return MAX_ENTITY_ID_BYTES; }
    function maxEntityNameBytes() external pure returns (uint256) { return MAX_ENTITY_NAME_BYTES; }
    function maxEntityDescriptionBytes() external pure returns (uint256) { return MAX_ENTITY_DESCRIPTION_BYTES; }
    function maxArcIdBytes() external pure returns (uint256) { return MAX_ARC_ID_BYTES; }
    function maxArcDescriptionBytes() external pure returns (uint256) { return MAX_ARC_DESCRIPTION_BYTES; }
}
