"""ABI definitions for CTF and NegRisk adapter contracts."""

from typing import Any


def _fn(
    name: str,
    inputs: list,
    outputs: list,
    *,
    mutability: str = "view",
) -> dict:
    """Build a minimal ABI function entry."""
    return {
        "inputs": inputs,
        "name": name,
        "outputs": outputs,
        "stateMutability": mutability,
        "type": "function",
    }


_UINT = [{"name": "", "type": "uint256"}]

CTF_ABI: list[dict[str, Any]] = [
    _fn(
        "redeemPositions",
        [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        [],
        mutability="nonpayable",
    ),
    _fn(
        "payoutDenominator",
        [{"name": "conditionId", "type": "bytes32"}],
        _UINT,
    ),
    _fn(
        "payoutNumerators",
        [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "index", "type": "uint256"},
        ],
        _UINT,
    ),
    _fn(
        "balanceOf",
        [
            {"name": "account", "type": "address"},
            {"name": "id", "type": "uint256"},
        ],
        _UINT,
    ),
    _fn(
        "isApprovedForAll",
        [
            {"name": "owner", "type": "address"},
            {"name": "operator", "type": "address"},
        ],
        [{"name": "", "type": "bool"}],
    ),
]

NEG_RISK_ABI: list[dict[str, Any]] = [
    _fn(
        "redeemPositions",
        [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "amounts", "type": "uint256[]"},
        ],
        [],
        mutability="nonpayable",
    ),
]
