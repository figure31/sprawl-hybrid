// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {Sprawl} from "../src/Sprawl.sol";

/// @notice Deploy the hybrid-mainnet Sprawl contract.
///
/// Usage:
///   set -a && . ./.env && set +a
///   forge script script/Deploy.s.sol --rpc-url sepolia --broadcast
///   forge script script/Deploy.s.sol --rpc-url mainnet --broadcast --verify
///
/// Required env:
///   DEPLOYER_PRIVATE_KEY   — the admin wallet that owns the contract
///   OPERATOR_ADDRESS       — address of the operator key that co-signs writes
///
/// Optional env (with defaults):
///   REGISTRATION_FEE       — default 0.005 ether
///   FIRST_SALE_PRICE       — default 0.0025 ether
contract Deploy is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address operator    = vm.envAddress("OPERATOR_ADDRESS");
        uint256 regFee      = vm.envOr("REGISTRATION_FEE", uint256(0.005 ether));
        uint256 salePrice   = vm.envOr("FIRST_SALE_PRICE", uint256(0.0025 ether));

        console2.log("Deployer:            ", vm.addr(deployerKey));
        console2.log("Operator:            ", operator);
        console2.log("Registration fee:    ", regFee);
        console2.log("First sale price:    ", salePrice);

        vm.startBroadcast(deployerKey);
        Sprawl sprawl = new Sprawl(regFee, salePrice, operator);
        vm.stopBroadcast();

        console2.log("Sprawl deployed at:  ", address(sprawl));
        console2.log("Domain separator:    ");
        console2.logBytes32(sprawl.DOMAIN_SEPARATOR());
    }
}
