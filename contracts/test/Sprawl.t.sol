// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {Sprawl} from "../src/Sprawl.sol";

/// @notice Core tests for the hybrid mainnet Sprawl contract. Exercises
/// registry, dual-signature collection, tamper rejection, reads, and the
/// marketplace. Marketplace-specific edge cases live in SprawlMarket.t.sol.
contract SprawlTest is Test {
    Sprawl s;

    // Test actors. Private keys are kept around so we can produce real
    // EIP-712 signatures via vm.sign.
    uint256 constant OWNER_PK    = 0x1;
    uint256 constant OPERATOR_PK = 0x2;
    uint256 constant ALICE_PK    = 0xA11CE;
    uint256 constant BOB_PK      = 0xB0B;
    uint256 constant CAROL_PK    = 0xCAFE;

    address owner    = vm.addr(OWNER_PK);
    address operator = vm.addr(OPERATOR_PK);
    address alice    = vm.addr(ALICE_PK);
    address bob      = vm.addr(BOB_PK);
    address carol    = vm.addr(CAROL_PK);

    uint256 constant REG_FEE   = 0.005 ether;
    uint256 constant SALE_FEE  = 0.0025 ether;

    // Cached typehashes matching the ones in the contract. If these ever
    // drift from the contract, every test will fail with BadAuthorSig —
    // exactly the signal we want.
    bytes32 constant LINK_TYPEHASH =
        keccak256("Link(uint256 linkId,uint256 parentId,uint64 authoredAt,uint64 nonce,uint64 beaconBlock,bool isRecap,uint256 coversFromId,uint256 coversToId,address author,string text)");
    bytes32 constant ENTITY_TYPEHASH =
        keccak256("Entity(string entityId,string name,string entityType,string description,uint64 authoredAt,uint64 nonce,uint64 beaconBlock,address author)");
    bytes32 constant ARC_TYPEHASH =
        keccak256("Arc(string arcId,uint256 anchorLinkId,string description,uint64 authoredAt,uint64 nonce,uint64 beaconBlock,address author)");

    bytes32 domainSeparator;

    function setUp() public {
        vm.startPrank(owner);
        s = new Sprawl(REG_FEE, SALE_FEE, operator);
        vm.stopPrank();

        domainSeparator = s.DOMAIN_SEPARATOR();

        vm.deal(owner,    100 ether);
        vm.deal(operator, 100 ether);
        vm.deal(alice,    100 ether);
        vm.deal(bob,      100 ether);
        vm.deal(carol,    100 ether);

        // Register alice and bob. Carol stays unregistered.
        vm.prank(alice); s.register{value: REG_FEE}("alice");
        vm.prank(bob);   s.register{value: REG_FEE}("bob");
    }

    // =====================================================================
    // Registry
    // =====================================================================

    function test_register_happyPath() public {
        vm.prank(carol); s.register{value: REG_FEE}("carol");
        (string memory name, bool isReg, bool isBanned,, uint64 registeredAt) = s.citizens(carol);
        assertEq(name, "carol");
        assertTrue(isReg);
        assertFalse(isBanned);
        assertGt(registeredAt, 0);
    }

    function test_register_refundsOverpayment() public {
        uint256 before = carol.balance;
        vm.prank(carol); s.register{value: 1 ether}("carol");
        // Balance dropped by exactly REG_FEE (plus no gas since this is a test env).
        assertEq(before - carol.balance, REG_FEE);
    }

    function test_register_rejectsBelowFee() public {
        vm.expectRevert();
        vm.prank(carol); s.register{value: REG_FEE - 1}("carol");
    }

    function test_register_rejectsDoubleRegister() public {
        vm.expectRevert(Sprawl.AlreadyRegistered.selector);
        vm.prank(alice); s.register{value: REG_FEE}("alice2");
    }

    function test_register_rejectsEmptyName() public {
        vm.expectRevert(Sprawl.NameEmpty.selector);
        vm.prank(carol); s.register{value: REG_FEE}("");
    }

    function test_rename_happyPath() public {
        vm.prank(alice); s.renameCitizen("alice-prime");
        (string memory name,,,,) = s.citizens(alice);
        assertEq(name, "alice-prime");
    }

    function test_rename_rejectsBanned() public {
        vm.prank(owner); s.banCitizen(alice);
        vm.expectRevert();
        vm.prank(alice); s.renameCitizen("alice-prime");
    }

    function test_banCitizen_onlyOwner() public {
        vm.expectRevert();
        vm.prank(bob); s.banCitizen(alice);
    }

    function test_banCitizen_unbanCitizen() public {
        vm.prank(owner); s.banCitizen(alice);
        (,, bool isBanned,,) = s.citizens(alice);
        assertTrue(isBanned);

        vm.prank(owner); s.unbanCitizen(alice);
        (,, isBanned,,) = s.citizens(alice);
        assertFalse(isBanned);
    }

    // =====================================================================
    // Collection — links
    // =====================================================================

    function test_collectLink_happyPath() public {
        uint256 linkId = 0xabc;
        bytes memory text = bytes("the city unfolds in small increments");

        (Sprawl.Sig memory authorSig, Sprawl.Sig memory operatorSig) =
            _signLink(linkId, 0, 100, 1, _beaconBlock(), false, 0, 0, alice, text);

        vm.prank(bob);
        s.collectLink{value: SALE_FEE}(
            linkId, 0, 100, 1, _beaconBlock(), false, 0, 0,
            alice, text, authorSig, operatorSig
        );

        Sprawl.LinkView memory v = s.readLink(linkId);
        assertEq(v.linkId, linkId);
        assertEq(v.creator, alice);
        assertEq(v.owner, bob);
        assertEq(v.parentId, 0);
        assertEq(v.text, text);
        assertFalse(v.isRecap);
        assertFalse(v.cleared);
    }

    function test_collectLink_splitsCorrectly() public {
        uint256 linkId = 0xabc;
        _collectOneLink(linkId, bob);

        // First sale: 75% protocol, 25% creator.
        uint256 expectedProto   = (SALE_FEE * 7500) / 10000;
        uint256 expectedCreator = SALE_FEE - expectedProto;
        // protocolBalance holds both regFees (alice + bob) + proto cut.
        assertEq(s.protocolBalance(), 2 * REG_FEE + expectedProto);
        assertEq(s.pendingWithdrawals(alice), expectedCreator);
    }

    function test_collectLink_rejectsTamperedText() public {
        uint256 linkId = 0xabc;
        bytes memory realText = bytes("original text alice signed");
        bytes memory fakeText = bytes("different text bob tries to collect");

        (Sprawl.Sig memory authorSig, Sprawl.Sig memory operatorSig) =
            _signLink(linkId, 0, 100, 1, _beaconBlock(), false, 0, 0, alice, realText);

        // Bob submits the real signatures but with different text. Should revert.
        vm.expectRevert(Sprawl.BadAuthorSig.selector);
        vm.prank(bob);
        s.collectLink{value: SALE_FEE}(
            linkId, 0, 100, 1, _beaconBlock(), false, 0, 0,
            alice, fakeText, authorSig, operatorSig
        );
    }

    function test_collectLink_rejectsTamperedParent() public {
        uint256 linkId = 0xabc;
        bytes memory text = bytes("alice's link");

        (Sprawl.Sig memory authorSig, Sprawl.Sig memory operatorSig) =
            _signLink(linkId, 5, 100, 1, _beaconBlock(), false, 0, 0, alice, text);

        // Bob swaps parentId from 5 to 99. Digest changes, sigs invalid.
        vm.expectRevert(Sprawl.BadAuthorSig.selector);
        vm.prank(bob);
        s.collectLink{value: SALE_FEE}(
            linkId, 99, 100, 1, _beaconBlock(), false, 0, 0,
            alice, text, authorSig, operatorSig
        );
    }

    function test_collectLink_rejectsTamperedAuthor() public {
        uint256 linkId = 0xabc;
        bytes memory text = bytes("alice's link");

        (Sprawl.Sig memory authorSig, Sprawl.Sig memory operatorSig) =
            _signLink(linkId, 0, 100, 1, _beaconBlock(), false, 0, 0, alice, text);

        // Bob claims bob is the author using alice's sig. Both sigs invalid.
        vm.expectRevert(Sprawl.BadAuthorSig.selector);
        vm.prank(bob);
        s.collectLink{value: SALE_FEE}(
            linkId, 0, 100, 1, _beaconBlock(), false, 0, 0,
            bob, text, authorSig, operatorSig
        );
    }

    function test_collectLink_rejectsNoOperatorSig() public {
        uint256 linkId = 0xabc;
        bytes memory text = bytes("alice's link");

        // Only author signed; operator sig replaced with random bytes that
        // will recover to some non-operator address.
        Sprawl.Sig memory authorSig = _signLinkAuthor(linkId, 0, 100, 1, _beaconBlock(), false, 0, 0, alice, text);
        Sprawl.Sig memory garbageOpSig = Sprawl.Sig({
            r: bytes32(uint256(1)),
            s: bytes32(uint256(1)),
            v: 27
        });

        vm.expectRevert(Sprawl.BadOperatorSig.selector);
        vm.prank(bob);
        s.collectLink{value: SALE_FEE}(
            linkId, 0, 100, 1, _beaconBlock(), false, 0, 0,
            alice, text, authorSig, garbageOpSig
        );
    }

    function test_collectLink_rejectsWrongOperator() public {
        uint256 linkId = 0xabc;
        bytes memory text = bytes("alice's link");

        // Alice signs correctly. A third party (carol's key) co-signs instead
        // of the real operator. Contract rejects because ecrecover(opSig)
        // returns carol, not the operator.
        Sprawl.Sig memory authorSig = _signLinkAuthor(linkId, 0, 100, 1, _beaconBlock(), false, 0, 0, alice, text);
        bytes32 digest = _linkDigest(linkId, 0, 100, 1, _beaconBlock(), false, 0, 0, alice, text);
        Sprawl.Sig memory carolSig = _signWith(CAROL_PK, digest);

        vm.expectRevert(Sprawl.BadOperatorSig.selector);
        vm.prank(bob);
        s.collectLink{value: SALE_FEE}(
            linkId, 0, 100, 1, _beaconBlock(), false, 0, 0,
            alice, text, authorSig, carolSig
        );
    }

    function test_collectLink_rejectsDoubleCollection() public {
        uint256 linkId = 0xabc;
        _collectOneLink(linkId, bob);

        (Sprawl.Sig memory authorSig, Sprawl.Sig memory operatorSig) =
            _signLink(linkId, 0, 100, 1, _beaconBlock(), false, 0, 0, alice, bytes("same"));

        vm.expectRevert(Sprawl.AlreadyCollected.selector);
        vm.prank(carol);
        s.collectLink{value: SALE_FEE}(
            linkId, 0, 100, 1, _beaconBlock(), false, 0, 0,
            alice, bytes("same"), authorSig, operatorSig
        );
    }

    function test_collectLink_rejectsBannedAuthor() public {
        uint256 linkId = 0xabc;
        bytes memory text = bytes("alice got banned after signing");

        (Sprawl.Sig memory authorSig, Sprawl.Sig memory operatorSig) =
            _signLink(linkId, 0, 100, 1, _beaconBlock(), false, 0, 0, alice, text);

        vm.prank(owner); s.banCitizen(alice);

        vm.expectRevert();
        vm.prank(bob);
        s.collectLink{value: SALE_FEE}(
            linkId, 0, 100, 1, _beaconBlock(), false, 0, 0,
            alice, text, authorSig, operatorSig
        );
    }

    function test_collectLink_rejectsWrongPayment() public {
        uint256 linkId = 0xabc;
        bytes memory text = bytes("alice's link");

        (Sprawl.Sig memory authorSig, Sprawl.Sig memory operatorSig) =
            _signLink(linkId, 0, 100, 1, _beaconBlock(), false, 0, 0, alice, text);

        vm.expectRevert();
        vm.prank(bob);
        s.collectLink{value: SALE_FEE - 1}(
            linkId, 0, 100, 1, _beaconBlock(), false, 0, 0,
            alice, text, authorSig, operatorSig
        );
    }

    function test_collectLink_recap() public {
        // Collect two links first so the recap range references real ids.
        _collectOneLink(1, bob);
        _collectOneLink(2, bob);

        uint256 recapId = 3;
        bytes memory text = bytes("summary of what happened so far");

        (Sprawl.Sig memory aSig, Sprawl.Sig memory oSig) =
            _signLink(recapId, 1, 200, 2, _beaconBlock(), true, 1, 2, alice, text);

        vm.prank(carol);
        s.collectLink{value: SALE_FEE}(
            recapId, 1, 200, 2, _beaconBlock(), true, 1, 2,
            alice, text, aSig, oSig
        );

        Sprawl.LinkView memory v = s.readLink(recapId);
        assertTrue(v.isRecap);
        assertEq(v.coversFromId, 1);
        assertEq(v.coversToId, 2);
    }

    function test_collectLink_rejectsInvalidRecapRange() public {
        uint256 linkId = 0xabc;
        bytes memory text = bytes("bad recap");

        (Sprawl.Sig memory aSig, Sprawl.Sig memory oSig) =
            _signLink(linkId, 0, 100, 1, _beaconBlock(), true, 5, 1, alice, text);

        vm.expectRevert(Sprawl.InvalidRecapRange.selector);
        vm.prank(bob);
        s.collectLink{value: SALE_FEE}(
            linkId, 0, 100, 1, _beaconBlock(), true, 5, 1,
            alice, text, aSig, oSig
        );
    }

    // =====================================================================
    // Collection — entities
    // =====================================================================

    function test_collectEntity_happyPath() public {
        string memory id = "hero";
        string memory name = "Hero";
        string memory etype = "character";
        string memory desc = "the protagonist";

        (Sprawl.Sig memory aSig, Sprawl.Sig memory oSig) =
            _signEntity(id, name, etype, desc, 100, 1, _beaconBlock(), alice);

        vm.prank(bob);
        s.collectEntity{value: SALE_FEE}(
            id, name, etype, desc, 100, 1, _beaconBlock(), alice, aSig, oSig
        );

        Sprawl.EntityView memory v = s.readEntity(keccak256(bytes(id)));
        assertEq(v.creator, alice);
        assertEq(v.owner, bob);
        // Content is packed as name || 0x00 || type || 0x00 || description.
        assertEq(v.content, abi.encodePacked(name, bytes1(0x00), etype, bytes1(0x00), desc));
    }

    function test_collectEntity_rejectsInvalidType() public {
        string memory id = "foo";
        string memory name = "Foo";
        string memory etype = "nonsense";
        string memory desc = "bad";

        // Sign anyway just to reach the type check.
        (Sprawl.Sig memory aSig, Sprawl.Sig memory oSig) =
            _signEntity(id, name, etype, desc, 100, 1, _beaconBlock(), alice);

        vm.expectRevert();
        vm.prank(bob);
        s.collectEntity{value: SALE_FEE}(
            id, name, etype, desc, 100, 1, _beaconBlock(), alice, aSig, oSig
        );
    }

    function test_collectEntity_rejectsTamperedName() public {
        string memory id = "hero";

        (Sprawl.Sig memory aSig, Sprawl.Sig memory oSig) =
            _signEntity(id, "Hero", "character", "real desc", 100, 1, _beaconBlock(), alice);

        vm.expectRevert(Sprawl.BadAuthorSig.selector);
        vm.prank(bob);
        s.collectEntity{value: SALE_FEE}(
            id, "Villain", "character", "real desc", 100, 1, _beaconBlock(), alice, aSig, oSig
        );
    }

    // =====================================================================
    // Collection — arcs
    // =====================================================================

    function test_collectArc_happyPath() public {
        // Need an anchor link that's already collected.
        _collectOneLink(1, bob);

        string memory arcId = "arc-one";
        string memory desc = "planning note for arc one";

        (Sprawl.Sig memory aSig, Sprawl.Sig memory oSig) =
            _signArc(arcId, 1, desc, 150, 1, _beaconBlock(), alice);

        vm.prank(carol);
        s.collectArc{value: SALE_FEE}(
            arcId, 1, desc, 150, 1, _beaconBlock(), alice, aSig, oSig
        );

        Sprawl.ArcView memory v = s.readArc(keccak256(bytes(arcId)));
        assertEq(v.creator, alice);
        assertEq(v.owner, carol);
        assertEq(v.anchorLinkId, 1);
        assertEq(v.description, bytes(desc));
    }

    function test_collectArc_rejectsMissingAnchor() public {
        string memory arcId = "arc-one";
        string memory desc = "orphan arc";

        (Sprawl.Sig memory aSig, Sprawl.Sig memory oSig) =
            _signArc(arcId, 999, desc, 150, 1, _beaconBlock(), alice);

        vm.expectRevert(Sprawl.AnchorLinkNotCollected.selector);
        vm.prank(bob);
        s.collectArc{value: SALE_FEE}(
            arcId, 999, desc, 150, 1, _beaconBlock(), alice, aSig, oSig
        );
    }

    // =====================================================================
    // Reads / reconstruction
    // =====================================================================

    function test_readLink_returnsBothSignatures() public {
        uint256 linkId = 0xabc;
        _collectOneLink(linkId, bob);

        Sprawl.LinkView memory v = s.readLink(linkId);
        // Both sigs should be present and non-empty.
        assertEq(v.authorSignature.length, 65);
        assertEq(v.operatorSignature.length, 65);
    }

    function test_readLink_revertsForUncollected() public {
        vm.expectRevert(Sprawl.AssetDoesNotExist.selector);
        s.readLink(0xdeadbeef);
    }

    // =====================================================================
    // Marketplace (resale)
    // =====================================================================

    function test_list_happyPath() public {
        uint256 linkId = 0xabc;
        _collectOneLink(linkId, bob);

        vm.prank(bob);
        s.list(Sprawl.AssetKind.Link, bytes32(linkId), 1 ether);
        assertEq(s.priceOf(Sprawl.AssetKind.Link, bytes32(linkId)), 1 ether);
    }

    function test_list_rejectsNotOwner() public {
        uint256 linkId = 0xabc;
        _collectOneLink(linkId, bob);

        vm.expectRevert(Sprawl.NotAssetOwner.selector);
        vm.prank(alice);
        s.list(Sprawl.AssetKind.Link, bytes32(linkId), 1 ether);
    }

    function test_list_rejectsZeroPrice() public {
        uint256 linkId = 0xabc;
        _collectOneLink(linkId, bob);

        vm.expectRevert(Sprawl.InvalidPrice.selector);
        vm.prank(bob);
        s.list(Sprawl.AssetKind.Link, bytes32(linkId), 0);
    }

    function test_buy_resaleSplitsCorrectly() public {
        uint256 linkId = 0xabc;
        _collectOneLink(linkId, bob);

        vm.prank(bob); s.list(Sprawl.AssetKind.Link, bytes32(linkId), 1 ether);

        uint256 protoBefore = s.protocolBalance();
        vm.prank(carol);
        s.buy{value: 1 ether}(Sprawl.AssetKind.Link, bytes32(linkId), 1 ether);

        uint256 protoCut   = (1 ether * 2500) / 10000;
        uint256 sellerCut  = 1 ether - protoCut;
        assertEq(s.protocolBalance(), protoBefore + protoCut);
        assertEq(s.pendingWithdrawals(bob), sellerCut);
        assertEq(s.ownerOf(Sprawl.AssetKind.Link, bytes32(linkId)), carol);
    }

    function test_buy_frontrunGuard() public {
        uint256 linkId = 0xabc;
        _collectOneLink(linkId, bob);

        vm.prank(bob); s.list(Sprawl.AssetKind.Link, bytes32(linkId), 1 ether);
        vm.prank(bob); s.list(Sprawl.AssetKind.Link, bytes32(linkId), 2 ether);

        vm.expectRevert(
            abi.encodeWithSelector(Sprawl.PriceMismatch.selector, uint256(2 ether), uint256(1 ether))
        );
        vm.prank(carol);
        s.buy{value: 1 ether}(Sprawl.AssetKind.Link, bytes32(linkId), 1 ether);
    }

    function test_withdraw_happyPath() public {
        uint256 linkId = 0xabc;
        _collectOneLink(linkId, bob);

        uint256 pending = s.pendingWithdrawals(alice);
        uint256 balBefore = alice.balance;
        vm.prank(alice); s.withdraw();
        assertEq(alice.balance, balBefore + pending);
        assertEq(s.pendingWithdrawals(alice), 0);
    }

    function test_withdraw_rejectsZero() public {
        vm.expectRevert(Sprawl.NothingToWithdraw.selector);
        vm.prank(carol); s.withdraw();
    }

    function test_accountingInvariant() public {
        // Run a mix: collection + resale. Invariant: contract balance must
        // equal protocolBalance + sum(pendingWithdrawals).
        _collectOneLink(1, bob);
        vm.prank(bob); s.list(Sprawl.AssetKind.Link, bytes32(uint256(1)), 1 ether);
        vm.prank(carol); s.buy{value: 1 ether}(Sprawl.AssetKind.Link, bytes32(uint256(1)), 1 ether);

        uint256 sumPending = s.pendingWithdrawals(alice)
                           + s.pendingWithdrawals(bob)
                           + s.pendingWithdrawals(carol);
        assertEq(address(s).balance, s.protocolBalance() + sumPending);

        // Drain everything; contract should hit zero.
        vm.prank(alice); s.withdraw();
        vm.prank(bob);   s.withdraw();
        vm.prank(owner); s.withdrawProtocol();
        assertEq(address(s).balance, 0);
    }

    // =====================================================================
    // Admin
    // =====================================================================

    function test_setOperator_rotation() public {
        address newOp = vm.addr(0x999);

        vm.prank(owner); s.setOperator(newOp);
        assertEq(s.operator(), newOp);

        // After rotation, old operator sigs no longer work.
        // Alice signs a new link; old operator (still at OPERATOR_PK) co-signs.
        uint256 linkId = 0xabc;
        bytes memory text = bytes("post-rotation");
        Sprawl.Sig memory aSig = _signLinkAuthor(linkId, 0, 100, 1, _beaconBlock(), false, 0, 0, alice, text);
        bytes32 digest = _linkDigest(linkId, 0, 100, 1, _beaconBlock(), false, 0, 0, alice, text);
        Sprawl.Sig memory oldOpSig = _signWith(OPERATOR_PK, digest);

        vm.expectRevert(Sprawl.BadOperatorSig.selector);
        vm.prank(bob);
        s.collectLink{value: SALE_FEE}(
            linkId, 0, 100, 1, _beaconBlock(), false, 0, 0,
            alice, text, aSig, oldOpSig
        );
    }

    function test_setPaused_blocksCollection() public {
        vm.prank(owner); s.setPaused(true);

        uint256 linkId = 0xabc;
        bytes memory text = bytes("during pause");
        (Sprawl.Sig memory aSig, Sprawl.Sig memory oSig) =
            _signLink(linkId, 0, 100, 1, _beaconBlock(), false, 0, 0, alice, text);

        vm.expectRevert(Sprawl.Paused.selector);
        vm.prank(bob);
        s.collectLink{value: SALE_FEE}(
            linkId, 0, 100, 1, _beaconBlock(), false, 0, 0,
            alice, text, aSig, oSig
        );
    }

    function test_clearLink_marksCleared() public {
        uint256 linkId = 0xabc;
        _collectOneLink(linkId, bob);

        vm.prank(owner); s.clearLink(linkId);
        Sprawl.LinkView memory v = s.readLink(linkId);
        assertTrue(v.cleared);
        // Content is still readable — clear only flags it.
        assertTrue(v.text.length > 0);
    }

    function test_setTreasury_routesWithdraw() public {
        address treas2 = vm.addr(0x777);

        _collectOneLink(1, bob);

        vm.prank(owner); s.setTreasury(treas2);
        uint256 expected = s.protocolBalance();

        vm.prank(owner); s.withdrawProtocol();
        assertEq(treas2.balance, expected);
    }

    // =====================================================================
    // Helpers
    // =====================================================================

    function _beaconBlock() internal view returns (uint64) {
        return uint64(block.number);
    }

    function _collectOneLink(uint256 linkId, address buyer) internal {
        bytes memory text = bytes("standard test link");
        (Sprawl.Sig memory aSig, Sprawl.Sig memory oSig) =
            _signLink(linkId, 0, uint64(block.timestamp), uint64(linkId), _beaconBlock(), false, 0, 0, alice, text);

        vm.prank(buyer);
        s.collectLink{value: SALE_FEE}(
            linkId, 0, uint64(block.timestamp), uint64(linkId), _beaconBlock(), false, 0, 0,
            alice, text, aSig, oSig
        );
    }

    function _linkDigest(
        uint256 linkId,
        uint256 parentId,
        uint64 authoredAt,
        uint64 nonce,
        uint64 beaconBlock,
        bool isRecap,
        uint256 coversFromId,
        uint256 coversToId,
        address author,
        bytes memory text
    ) internal view returns (bytes32) {
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
        return keccak256(abi.encodePacked(bytes2(0x1901), domainSeparator, structHash));
    }

    function _entityDigest(
        string memory id,
        string memory name,
        string memory etype,
        string memory desc,
        uint64 authoredAt,
        uint64 nonce,
        uint64 beaconBlock,
        address author
    ) internal view returns (bytes32) {
        bytes32 structHash = keccak256(abi.encode(
            ENTITY_TYPEHASH,
            keccak256(bytes(id)),
            keccak256(bytes(name)),
            keccak256(bytes(etype)),
            keccak256(bytes(desc)),
            authoredAt,
            nonce,
            beaconBlock,
            author
        ));
        return keccak256(abi.encodePacked(bytes2(0x1901), domainSeparator, structHash));
    }

    function _arcDigest(
        string memory arcId,
        uint256 anchorLinkId,
        string memory desc,
        uint64 authoredAt,
        uint64 nonce,
        uint64 beaconBlock,
        address author
    ) internal view returns (bytes32) {
        bytes32 structHash = keccak256(abi.encode(
            ARC_TYPEHASH,
            keccak256(bytes(arcId)),
            anchorLinkId,
            keccak256(bytes(desc)),
            authoredAt,
            nonce,
            beaconBlock,
            author
        ));
        return keccak256(abi.encodePacked(bytes2(0x1901), domainSeparator, structHash));
    }

    function _signWith(uint256 pk, bytes32 digest) internal pure returns (Sprawl.Sig memory) {
        (uint8 v, bytes32 r, bytes32 sSig) = vm.sign(pk, digest);
        return Sprawl.Sig({r: r, s: sSig, v: v});
    }

    function _signLinkAuthor(
        uint256 linkId,
        uint256 parentId,
        uint64 authoredAt,
        uint64 nonce,
        uint64 beaconBlock,
        bool isRecap,
        uint256 coversFromId,
        uint256 coversToId,
        address author,
        bytes memory text
    ) internal view returns (Sprawl.Sig memory) {
        bytes32 digest = _linkDigest(linkId, parentId, authoredAt, nonce, beaconBlock, isRecap, coversFromId, coversToId, author, text);
        // Only alice/bob are registered in setUp; pick their PK.
        uint256 pk = author == alice ? ALICE_PK : (author == bob ? BOB_PK : CAROL_PK);
        return _signWith(pk, digest);
    }

    function _signLink(
        uint256 linkId,
        uint256 parentId,
        uint64 authoredAt,
        uint64 nonce,
        uint64 beaconBlock,
        bool isRecap,
        uint256 coversFromId,
        uint256 coversToId,
        address author,
        bytes memory text
    ) internal view returns (Sprawl.Sig memory authorSig, Sprawl.Sig memory operatorSig) {
        bytes32 digest = _linkDigest(linkId, parentId, authoredAt, nonce, beaconBlock, isRecap, coversFromId, coversToId, author, text);
        uint256 authorPk = author == alice ? ALICE_PK : (author == bob ? BOB_PK : CAROL_PK);
        authorSig = _signWith(authorPk, digest);
        operatorSig = _signWith(OPERATOR_PK, digest);
    }

    function _signEntity(
        string memory id,
        string memory name,
        string memory etype,
        string memory desc,
        uint64 authoredAt,
        uint64 nonce,
        uint64 beaconBlock,
        address author
    ) internal view returns (Sprawl.Sig memory authorSig, Sprawl.Sig memory operatorSig) {
        bytes32 digest = _entityDigest(id, name, etype, desc, authoredAt, nonce, beaconBlock, author);
        uint256 authorPk = author == alice ? ALICE_PK : (author == bob ? BOB_PK : CAROL_PK);
        authorSig = _signWith(authorPk, digest);
        operatorSig = _signWith(OPERATOR_PK, digest);
    }

    function _signArc(
        string memory arcId,
        uint256 anchorLinkId,
        string memory desc,
        uint64 authoredAt,
        uint64 nonce,
        uint64 beaconBlock,
        address author
    ) internal view returns (Sprawl.Sig memory authorSig, Sprawl.Sig memory operatorSig) {
        bytes32 digest = _arcDigest(arcId, anchorLinkId, desc, authoredAt, nonce, beaconBlock, author);
        uint256 authorPk = author == alice ? ALICE_PK : (author == bob ? BOB_PK : CAROL_PK);
        authorSig = _signWith(authorPk, digest);
        operatorSig = _signWith(OPERATOR_PK, digest);
    }
}
