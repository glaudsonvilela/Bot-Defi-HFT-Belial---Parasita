"""FIX4: token-program aware binary validation for a READ-ONLY spot sensor.

Exact legacy SPL Token or Token-2022 program, never a ticker heuristic. This is
NOT unrestricted Token-2022 support. Only the explicit metadata/invariant subset
below is accepted. Economic/opaque/unknown extensions fail closed, even when a
present transfer-fee rate is zero. No wallet, signatures or transactions.
Source ABI: solana-program/token-2022 interface (sources in FONTES_FIX4.md).
"""
from __future__ import annotations
import base64
import hashlib
from typing import Any

TOKEN = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN_2022 = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
PROGRAMS = (TOKEN, TOKEN_2022)
REVISION = 'FIX4_TOKEN_CAPABILITIES_V1'
ALPH = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
MAX_BYTES = 1024 * 1024
# (name, account kind, fixed length or None for Borsh variable data)
EXTENSIONS = {
    3: ('MintCloseAuthority', 'mint', 32),
    6: ('DefaultAccountState', 'mint', 1),
    7: ('ImmutableOwner', 'vault', 0),
    8: ('MemoTransfer', 'vault', 1),
    11: ('CpiGuard', 'vault', 1),
    18: ('MetadataPointer', 'mint', 64),
    19: ('TokenMetadata', 'mint', None),
    20: ('GroupPointer', 'mint', 64),
    21: ('TokenGroup', 'mint', 80),
    22: ('GroupMemberPointer', 'mint', 64),
    23: ('TokenGroupMember', 'mint', 72),
}
BLOCKED = {1:'TransferFeeConfig',2:'TransferFeeAmount',4:'ConfidentialTransferMint',
    5:'ConfidentialTransferAccount',9:'NonTransferable',10:'InterestBearingConfig',
    12:'PermanentDelegate',13:'NonTransferableAccount',14:'TransferHook',
    15:'TransferHookAccount',16:'ConfidentialTransferFeeConfig',
    17:'ConfidentialTransferFeeAmount',24:'ConfidentialMintBurn',25:'ScaledUiAmount',
    26:'Pausable',27:'PausableAccount',28:'PermissionedBurn'}


def b58(raw: bytes) -> str:
    n = int.from_bytes(raw, 'big'); out = ''
    while n:
        n, d = divmod(n, 58); out = ALPH[d] + out
    return '1' * (len(raw) - len(raw.lstrip(b'\0'))) + out


def _coption(raw: bytes, offset: int) -> bool:
    tag = int.from_bytes(raw[offset:offset + 4], 'little')
    if tag not in (0, 1):
        raise RuntimeError('TOKEN_COPTION_INVALID:' + str(offset))
    return bool(tag)


def _envelope(value: Any, address: str, kind: str) -> tuple[str, bytes]:
    prefix = 'MINT' if kind == 'mint' else 'VAULT'
    if not isinstance(value, dict) or value.get('owner') not in PROGRAMS or value.get('executable') is not False:
        owner = value.get('owner') if isinstance(value, dict) else None
        raise RuntimeError(f'{prefix}_OWNER_UNSUPPORTED:{address}:owner={owner!r}')
    data = value.get('data')
    if not isinstance(data, list) or len(data) != 2 or data[1] != 'base64' or not isinstance(data[0], str):
        raise RuntimeError(f'{prefix}_ENCODING_INVALID:{address}')
    if len(data[0]) > (MAX_BYTES + 2) // 3 * 4:
        raise RuntimeError('TOKEN_ACCOUNT_TOO_LARGE')
    try:
        raw = base64.b64decode(data[0], validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f'{prefix}_BASE64_INVALID:{address}') from exc
    if len(raw) > MAX_BYTES:
        raise RuntimeError('TOKEN_ACCOUNT_TOO_LARGE')
    return value['owner'], raw


def _metadata(payload: bytes, mint: str) -> None:
    if len(payload) < 80 or b58(payload[32:64]) != mint:
        raise RuntimeError('TOKEN_METADATA_MINT_OR_LAYOUT')
    pos = 64
    def uint() -> int:
        nonlocal pos
        if pos + 4 > len(payload):
            raise RuntimeError('TOKEN_METADATA_TRUNCATED')
        n = int.from_bytes(payload[pos:pos + 4], 'little'); pos += 4
        return n
    def string() -> None:
        nonlocal pos
        n = uint()
        if pos + n > len(payload):
            raise RuntimeError('TOKEN_METADATA_STRING_LENGTH')
        try:
            payload[pos:pos + n].decode('utf-8')
        except UnicodeDecodeError as exc:
            raise RuntimeError('TOKEN_METADATA_UTF8') from exc
        pos += n
    for _ in range(3):
        string()
    pairs = uint()
    if pairs > (len(payload) - pos) // 8:
        raise RuntimeError('TOKEN_METADATA_PAIR_COUNT')
    for _ in range(pairs):
        string(); string()
    # Token metadata may be resized with zeroed unused allocation.
    if any(payload[pos:]):
        raise RuntimeError('TOKEN_METADATA_TRAILING_DATA')


def extensions(raw: bytes, program: str, kind: str, mint: str) -> list[dict]:
    base_len = 82 if kind == 'mint' else 165
    if len(raw) < base_len or len(raw) == 355:
        raise RuntimeError('TOKEN_LAYOUT_OR_MULTISIG')
    if program == TOKEN:
        if len(raw) != base_len:
            raise RuntimeError('LEGACY_TOKEN_UNEXPECTED_EXTENSION_BYTES')
        return []
    if program != TOKEN_2022:
        raise RuntimeError('TOKEN_PROGRAM_UNSUPPORTED')
    if len(raw) == base_len:
        return []
    if len(raw) < 166 or raw[165] != (1 if kind == 'mint' else 2):
        raise RuntimeError('TOKEN2022_ACCOUNT_TYPE_OR_LENGTH')
    if kind == 'mint' and any(raw[82:165]):
        raise RuntimeError('TOKEN2022_MINT_PADDING')
    pos = 166; seen = set(); result = []
    while pos < len(raw):
        if len(raw) - pos < 2:
            if any(raw[pos:]):
                raise RuntimeError('TOKEN2022_TLV_TRUNCATED_TYPE')
            break
        tag = int.from_bytes(raw[pos:pos + 2], 'little')
        if tag == 0:
            if any(raw[pos:]):
                raise RuntimeError('TOKEN2022_TLV_NONZERO_AFTER_TERMINATOR')
            break
        if len(raw) - pos < 4:
            raise RuntimeError('TOKEN2022_TLV_TRUNCATED_LENGTH')
        size = int.from_bytes(raw[pos + 2:pos + 4], 'little'); pos += 4
        if pos + size > len(raw):
            raise RuntimeError('TOKEN2022_TLV_OUT_OF_BOUNDS')
        payload = raw[pos:pos + size]; pos += size
        if tag in seen:
            raise RuntimeError('TOKEN2022_DUPLICATE_EXTENSION:' + str(tag))
        seen.add(tag)
        if tag not in EXTENSIONS:
            raise RuntimeError('TOKEN2022_EXTENSION_UNSUPPORTED:' + BLOCKED.get(tag, 'UNKNOWN_' + str(tag)))
        name, expected_kind, length = EXTENSIONS[tag]
        if expected_kind != kind:
            raise RuntimeError('TOKEN2022_EXTENSION_WRONG_ACCOUNT_TYPE:' + name)
        if length is not None and size != length:
            raise RuntimeError('TOKEN2022_EXTENSION_LENGTH:' + name)
        if tag == 6 and payload != b'\1':
            raise RuntimeError('TOKEN2022_DEFAULT_ACCOUNT_STATE_NOT_INITIALIZED')
        if tag in (8, 11) and payload not in (b'\0', b'\1'):
            raise RuntimeError('TOKEN2022_EXTENSION_BOOL:' + name)
        if tag == 19:
            _metadata(payload, mint)
        if tag == 21:
            if b58(payload[32:64]) != mint or int.from_bytes(payload[64:72], 'little') > int.from_bytes(payload[72:80], 'little'):
                raise RuntimeError('TOKEN2022_GROUP_MINT_OR_SIZE')
        if tag == 23 and b58(payload[:32]) != mint:
            raise RuntimeError('TOKEN2022_GROUP_MEMBER_MINT')
        result.append({'id':tag,'name':name,'length':size})
    return result


def validate_mint(value: Any, address: str) -> dict:
    program, raw = _envelope(value, address, 'mint')
    if len(raw) < 82 or raw[45] != 1:
        raise RuntimeError('MINT_LAYOUT_OR_INITIALIZATION:' + address)
    if raw[44] > 18:
        raise RuntimeError('MINT_DECIMALS_UNSUPPORTED:' + address)
    mint_authority = _coption(raw, 0); freeze_authority = _coption(raw, 46)
    ext = extensions(raw, program, 'mint', address)
    return {'address':address,'program':program,'family':'TOKEN_2022' if program == TOKEN_2022 else 'SPL_TOKEN',
        'decimals':raw[44],'supply_raw':str(int.from_bytes(raw[36:44], 'little')),
        'mint_authority_present':mint_authority,'freeze_authority_present':freeze_authority,
        'extensions':ext,'account_len':len(raw),'bytes_sha256':hashlib.sha256(raw).hexdigest(),
        'revision':REVISION,'amount_basis':'RAW_INTEGER_DIV_10_POW_DECIMALS',
        'scope':'READ_ONLY_SPOT_SENSOR_NOT_TOKEN_RISK_APPROVAL','ok':True}


def validate_vault(value: Any, mint: str, expected_program: str, address: str = '') -> dict:
    program, raw = _envelope(value, address, 'vault')
    if program != expected_program:
        raise RuntimeError('VAULT_MINT_PROGRAM_MISMATCH:' + address)
    if len(raw) < 165 or raw[108] != 1 or b58(raw[:32]) != mint:
        raise RuntimeError('VAULT_LAYOUT_MINT_OR_STATE:' + address)
    for offset in (72,109,129):
        _coption(raw, offset)
    ext = extensions(raw, program, 'vault', mint)
    return {'address':address,'program':program,'mint':mint,'account_len':len(raw),'extensions':ext,
        'amount_raw':str(int.from_bytes(raw[64:72], 'little')),
        'bytes_sha256':hashlib.sha256(raw).hexdigest(),'revision':REVISION,'ok':True}


def verify_manifest(manifest: dict) -> None:
    """Recheck recorded token bytes before any live process is launched.

    This is structural revalidation of the selected RPC snapshot, not a fresh
    on-chain read or proof of current reserve quantities.
    """
    if manifest.get('ok') is not True or manifest.get('token_validation_revision') != REVISION:
        raise RuntimeError('FIX4_APPROVED_TOKEN_MANIFEST_REQUIRED')
    pools = manifest.get('pools')
    if not isinstance(pools, list) or len(pools) != 12 or len({r['pool'] for r in pools}) != 12:
        raise RuntimeError('FIX4_TWELVE_TOKEN_CONTRACTS_REQUIRED')
    for row in pools:
        if row.get('token_contract_revision') != REVISION:
            raise RuntimeError('FIX4_TOKEN_CONTRACT_REVISION:' + row.get('symbol','?'))
        for side in ('coin','pc'):
            program = row.get(side + '_token_program'); mint = row[side + '_mint']
            value = {'owner':program,'executable':False,'data':[row.get(side + '_mint_data_b64'),'base64']}
            profile = validate_mint(value, mint)
            expected = row.get(side + '_mint_capabilities') or {}
            if profile != expected or profile['decimals'] != row[side + '_decimals']:
                raise RuntimeError('FIX4_MINT_EVIDENCE_CHANGED:' + row['symbol'])
            if row['pool_kind'] == 'AMM_V4':
                if program != TOKEN:
                    raise RuntimeError('LEGACY_AMM_REQUIRES_LEGACY_TOKEN')
            else:
                value = {'owner':program,'executable':False,'data':[row.get(side + '_vault_data_b64'),'base64']}
                profile = validate_vault(value, mint, program, row[side + '_vault'])
                if profile != row.get(side + '_vault_capabilities'):
                    raise RuntimeError('FIX4_VAULT_EVIDENCE_CHANGED:' + row['symbol'])
