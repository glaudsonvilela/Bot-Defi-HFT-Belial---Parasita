"""Explicit cluster identity. Do not infer mainnet from the endpoint hostname."""
from a51.common import atomic,now_ms
# Solana SDK ClusterType::MainnetBeta::get_genesis_hash:
# https://github.com/solana-labs/solana/blob/master/sdk/src/genesis_config.rs
MAINNET_GENESIS='5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d'

def check_solana_mainnet(rpc,session):
    observed=rpc.call('getGenesisHash')
    valid=isinstance(observed,str) and len(observed)<=64 and all(c in '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz' for c in observed)
    passed=observed==MAINNET_GENESIS
    atomic(session/'NETWORK_CHECK_SOLANA.json',dict(at_ms=now_ms(),expected_genesis=MAINNET_GENESIS,
        observed_genesis=observed if valid else None,response_type=type(observed).__name__,passed=passed,orders=False),True)
    if not passed:raise RuntimeError('SOLANA_MAINNET_GENESIS_MISMATCH')
