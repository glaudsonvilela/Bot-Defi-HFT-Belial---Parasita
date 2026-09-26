#!/usr/bin/env python3
"""T3 FIX4: token capability validation + FIX2 decoders + FIX3 RPC/WS connection.

No symbol lookup is an identity authority. Only explicitly supported Raydium TOKEN-WSOL layouts can pass. Public APIs are metadata only, not the price feed or trading APIs.
A failure prevents the session; no substitute token and no partial 12-pool run.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import math
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from rpc_transport import Client, RpcFailure, profiles
from rpc_selection import select as select_rpc
import token_capabilities as tc

WSOL = 'So11111111111111111111111111111111111111112'
AMM = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'
CPMM = 'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C'
CLMM = 'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK'
SUPPORTED = {AMM:'AMM_V4', CPMM:'CPMM', CLMM:'CLMM'}
POOL_DISCRIMINATOR = hashlib.sha256(b'account:PoolState').digest()[:8]
CONFIG_DISCRIMINATOR = hashlib.sha256(b'account:AmmConfig').digest()[:8]
TOKEN = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
SYMBOLS = ('FARTCOIN','BOME','WIF','POPCAT','RAY','JUP','BONK','PENGU','PUMP','USELESS','GRASS','PYTH')
ORIGINAL = [
 ('FARTCOIN','Bzc9NZfMqkXR6fz1DBph7BDf9BroyEf6pnzESP7v5iiw','9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump'),
 ('BOME','DSUvc5qf5LJHHV5e2tD184ixotSnCnwj7i4jJa4Xsrmt','ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82'),
 ('WIF','EP2ib6dYdEeqD8MfE2ezHCxX3kP3K2eLKkirfPm5eyMx','EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm'),
 ('POPCAT','FRhB8L7Y9Qq41qZXYLtC2nw8An1RJfLLxRF2x9RwLLMo','7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr'),
 ('RAY','AVs9TA4nWDzfPJE9gGVNJMVhcQy3V9PGazuz33BfG2RA','4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R'),
 ('JUP','EYErUp5muPYEEkeaUCY22JibeZX7E9UuMcJFZkmNAN7c','JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN')]
CANONICAL_MINTS = {
 'BONK':'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263',
 'PENGU':'2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv',
 'PUMP':'pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn',
 'USELESS':'Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk',
 'GRASS':'Grass7B4RdKfBCjTKgSqnXkqjwiGvQyFbuSCUJr3XXjs',
 'PYTH':'HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3'}
ALPH = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
MAX_PAGES = 3
RESOLVE_SECONDS_PER_TOKEN = 60.0
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


def b58(b: bytes) -> str:
    n = int.from_bytes(b, 'big'); s = ''
    while n:
        n, digit = divmod(n, 58); s = ALPH[digit] + s
    return '1' * (len(b) - len(b.lstrip(b'\0'))) + s


def pubkey_bytes(value: str) -> bytes:
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        raise RuntimeError('PUBKEY_LENGTH_INVALID')
    number = 0
    for char in value:
        if char not in ALPH:
            raise RuntimeError('PUBKEY_ALPHABET_INVALID')
        number = number * 58 + ALPH.index(char)
    raw = b'\0' * (len(value) - len(value.lstrip('1')))
    raw += number.to_bytes((number.bit_length() + 7) // 8, 'big')
    if len(raw) != 32 or b58(raw) != value:
        raise RuntimeError('PUBKEY_NOT_CANONICAL_32_BYTES')
    return raw


def canonical_mints() -> dict[str, str]:
    """Validate the local source manifest. Never promote a ticker to identity."""
    manifest = json.loads((Path(__file__).resolve().parents[1] / 'CANONICAL_MINTS.json').read_text())
    mapped = {s: value['mint'] for s, value in manifest['tokens'].items()}
    if manifest.get('network') != 'solana-mainnet-beta' or mapped != CANONICAL_MINTS:
        raise RuntimeError('CANONICAL_MINT_MANIFEST_MISMATCH')
    if set(mapped) != set(SYMBOLS[6:]):
        raise RuntimeError('CANONICAL_MINT_UNIVERSE_MISMATCH')
    all_mints = [m for _, _, m in ORIGINAL] + list(mapped.values())
    if len(set(all_mints)) != 12 or WSOL in all_mints:
        raise RuntimeError('CANONICAL_MINT_DUPLICATE_OR_WRONG_QUOTE')
    for mint in all_mints + [WSOL, AMM, TOKEN]:
        pubkey_bytes(mint)
    return dict(mapped)


def save_json(path: Path | None, value: Any) -> None:
    if path is None:
        return
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    os.replace(temp, path)


def http(url: str, body: Any = None, *, deadline: float | None = None) -> Any:
    if body is not None:
        return Client(url, deadline=deadline).request(body)
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers={
        'Content-Type':'application/json', 'User-Agent':'Belial-T3-9H-FIX2-PAPER/1'})
    last: Exception | None = None
    for attempt in range(2):
        remaining = 10.0 if deadline is None else min(10.0, deadline - time.monotonic())
        if remaining <= 0:
            raise RuntimeError('METADATA_DEADLINE_EXCEEDED')
        try:
            with urllib.request.urlopen(request, timeout=remaining) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise RuntimeError('RESPONSE_TOO_LARGE')
            # Reject NaN/Infinity instead of allowing non-standard financial data.
            return json.loads(raw, parse_constant=lambda x: (_ for _ in ()).throw(ValueError('NONFINITE_JSON:'+x)))
        except Exception as exc:
            last = exc
            if attempt == 0:
                remaining = .5 if deadline is None else max(0, min(.5, deadline - time.monotonic()))
                time.sleep(remaining)
    raise RuntimeError('METADATA_HTTP_FAILED:' + str(last))


def rpc(url: str, keys: list[str], client=None):
    request = client.request if client is not None else lambda body: http(url, body)
    value = request({'jsonrpc':'2.0','id':1,'method':'getMultipleAccounts',
        'params':[keys, {'encoding':'base64','commitment':'confirmed'}]})
    if not isinstance(value, dict):
        raise RuntimeError('RPC_SCHEMA_INVALID')
    if value.get('error'):
        raise RuntimeError('RPC:' + str(value['error']))
    result = value.get('result') or {}; accounts = result.get('value')
    if not isinstance(accounts, list) or len(accounts) != len(keys):
        raise RuntimeError('RPC_ACCOUNT_COUNT_MISMATCH')
    slot = (result.get('context') or {}).get('slot')
    if type(slot) is not int or slot < 0:
        raise RuntimeError('RPC_SLOT_INVALID')
    return accounts, slot


def pool_page(value: Any) -> tuple[list[dict], dict]:
    if not isinstance(value, dict) or value.get('success') is not True:
        raise RuntimeError('RAYDIUM_POOL_METADATA_FAILED')
    data = value.get('data')
    rows = data.get('data') if isinstance(data, dict) else data
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError('RAYDIUM_POOL_SCHEMA_CHANGED')
    paging = dict(data) if isinstance(data, dict) else {}
    for name in ('nextPageId', 'hasNextPage'):
        if name not in paging and name in value:
            paging[name] = value[name]
    return rows, paging


def compatible(row: dict, mint: str) -> bool:
    a = row.get('mintA'); b = row.get('mintB')
    return (isinstance(a, dict) and isinstance(b, dict)
        and row.get('programId') == AMM
        and {a.get('address'), b.get('address')} == {mint, WSOL}
        and isinstance(row.get('id'), str) and bool(row['id']))


def liquidity(row: dict) -> float:
    try:
        value = float(row.get('tvl') or 0)
    except (ValueError, TypeError):
        return -1
    return value if math.isfinite(value) and value >= 0 else -1


def compatible_supported(row: dict, mint: str) -> bool:
    a, b = row.get('mintA'), row.get('mintB')
    return (isinstance(a, dict) and isinstance(b, dict)
        and row.get('programId') in SUPPORTED
        and {a.get('address'), b.get('address')} == {mint, WSOL}
        and isinstance(row.get('id'), str) and bool(row['id']))


def choose_supported(value: Any, mint: str) -> dict:
    rows, _ = pool_page(value)
    good = [r for r in rows if compatible_supported(r, mint) and liquidity(r) > 0]
    if not good:
        raise RuntimeError('NO_COMPATIBLE_SUPPORTED_TOKEN_SOL_POOL')
    return sorted(good, key=lambda r:(-liquidity(r), r['id']))[0]


def choose_legacy(value: Any, mint: str) -> dict:
    rows, _ = pool_page(value)
    good = [row for row in rows if compatible(row, mint) and liquidity(row) >= 0]
    if not good:
        raise RuntimeError('NO_COMPATIBLE_LEGACY_AMM_V4_TOKEN_SOL_POOL')
    return sorted(good, key=lambda row: (-liquidity(row), row['id']))[0]


def discovery_url(mint: str, style: str, page: int = 1, cursor: str | None = None) -> str:
    first, second = sorted([mint, WSOL])
    if style == 'list-v2':
        query = {'size':100,'mint1':first,'mint2':second,# All pool families: omit poolType, as in the official SDK.
                 
                 'sortField':'liquidity','sortType':'desc'}
        if cursor:
            query['nextPageId'] = cursor
        endpoint = '/pools/info/list-v2'
    elif style == 'legacy':
        query = {'mint1':mint,'mint2':WSOL,'poolType':'all',
                 'poolSortField':'liquidity','sortType':'desc','pageSize':100,'page':page}
        endpoint = '/pools/info/mint'
    else:
        raise RuntimeError('DISCOVERY_STYLE_INVALID')
    return 'https://api-v3.raydium.io' + endpoint + '?' + urllib.parse.urlencode(query)


def resolve_pool(sym: str, mint: str, evidence_dir: Path | None = None) -> tuple[dict, dict]:
    """Bounded, schema-aware discovery. Exact mints and owner must still pass RPC.

    Ranking is among compatible candidates on retrieved pages, NOT a claim of
    exhaustive liquidity coverage. Endpoint failure is recorded, not called absence.
    """
    deadline = time.monotonic() + RESOLVE_SECONDS_PER_TOKEN
    attempts = []
    for style in ('list-v2', 'legacy'):
        cursor = None; seen_cursors = set()
        for page in range(1, MAX_PAGES + 1):
            endpoint = discovery_url(mint, style, page, cursor)
            attempt: dict[str, Any] = {'endpoint':endpoint, 'style':style, 'page':page}
            attempts.append(attempt)
            try:
                value = http(endpoint, deadline=deadline)
                if evidence_dir is not None:
                    save_json(Path(evidence_dir) / f'{sym}_{style}_{page}.json', {'url':endpoint,'response':value})
                rows, paging = pool_page(value)
                attempt.update({'response_rows':len(rows), 'compatible_rows':sum(compatible_supported(x,mint) for x in rows),
                    'programs_seen':sorted({str(x.get('programId')) for x in rows}),
                    'response_json_sha256':hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()})
                try:
                    selected = choose_supported(value, mint)
                except RuntimeError as exc:
                    if str(exc) != 'NO_COMPATIBLE_SUPPORTED_TOKEN_SOL_POOL':
                        raise
                else:
                    pubkey_bytes(selected['id'])
                    result = {'identity':'PINNED_MAINNET_MINT','selection':'highest TVL compatible candidate on retrieved page; RPC required',
                        'tvl_metadata':liquidity(selected),'attempts':attempts,'selected_pool':selected['id'],'program_id':selected['programId'],
                        'pool_kind':SUPPORTED[selected['programId']],'volume24h_metadata':(selected.get('day') or {}).get('volume')}
                    return selected, result
                next_cursor = paging.get('nextPageId')
                if style == 'list-v2':
                    if isinstance(next_cursor, str) and next_cursor:
                        if next_cursor in seen_cursors:
                            raise RuntimeError('PAGINATION_CURSOR_REPEATED')
                        seen_cursors.add(next_cursor); cursor = next_cursor
                    else:
                        if paging.get('hasNextPage') is True:
                            attempt['pagination_note'] = 'hasNextPage without cursor; trying legacy paged endpoint'
                        break
                elif paging.get('hasNextPage') is not True:
                    break
                if page == MAX_PAGES:
                    attempt['pagination_note'] = 'MAX_PAGES_REACHED_NOT_EXHAUSTIVE'
            except Exception as exc:
                attempt['error'] = str(exc)
                break
    code = ('POOL_DISCOVERY_INCOMPLETE_OR_API_ERROR' if any('error' in a or 'pagination_note' in a for a in attempts)
            else 'NO_COMPATIBLE_SUPPORTED_TOKEN_SOL_POOL')
    details = {'symbol':sym,'mint':mint,'error':code,'attempts':attempts,'scan_started':False}
    if evidence_dir is not None:
        save_json(Path(evidence_dir) / f'{sym}_FAILED.json', details)
    raise RuntimeError(f'{sym}:{code}')


def validate_pool(sym, pool, mint, value):
    owner = (value or {}).get('owner')
    if owner not in SUPPORTED or value.get('executable'):
        raise RuntimeError(f'{sym}:OWNER_OR_ACCOUNT_INVALID')
    raw = base64.b64decode(value['data'][0], validate=True)
    kind = SUPPORTED[owner]
    extra = {}
    if owner == AMM:
        if len(raw) != 752:
            raise RuntimeError(f'{sym}:UNSUPPORTED_LAYOUT:{len(raw)}')
        coin, pc = b58(raw[400:432]), b58(raw[432:464])
        d1, d2 = int.from_bytes(raw[32:40],'little'), int.from_bytes(raw[40:48],'little')
    else:
        expected_len = 637 if owner == CPMM else 1544
        if len(raw) != expected_len:
            raise RuntimeError(f'{sym}:UNSUPPORTED_LAYOUT:{kind}:{len(raw)}')
        if raw[:8] != POOL_DISCRIMINATOR:
            raise RuntimeError(f'{sym}:POOL_DISCRIMINATOR_MISMATCH')
        if owner == CPMM:
            coin, pc = b58(raw[168:200]), b58(raw[200:232])
            d1, d2 = raw[331], raw[332]
            if b58(raw[232:264]) not in tc.PROGRAMS or b58(raw[264:296]) not in tc.PROGRAMS:
                raise RuntimeError(f'{sym}:TOKEN_PROGRAM_UNSUPPORTED')
            if raw[329] & 4:
                raise RuntimeError(f'{sym}:SWAPS_DISABLED')
            config, v0, v1 = b58(raw[8:40]), b58(raw[72:104]), b58(raw[104:136])
        else:
            coin, pc = b58(raw[73:105]), b58(raw[105:137])
            d1, d2 = raw[233], raw[234]
            if raw[389] & 16:
                raise RuntimeError(f'{sym}:SWAPS_DISABLED')
            if int.from_bytes(raw[253:269],'little') <= 0:
                raise RuntimeError(f'{sym}:SQRT_PRICE_INVALID')
            config, v0, v1 = b58(raw[9:41]), b58(raw[137:169]), b58(raw[169:201])
        for key in (config, v0, v1):
            if key == '1'*32: raise RuntimeError(f'{sym}:ZERO_CONFIG_OR_VAULT')
            pubkey_bytes(key)
        if v0 == v1:
            raise RuntimeError(f'{sym}:DUPLICATE_VAULT')
        extra = {'amm_config':config, 'coin_vault':v0, 'pc_vault':v1}
        if owner == CPMM:
            extra.update(coin_token_program=b58(raw[232:264]), pc_token_program=b58(raw[264:296]))
    if {coin, pc} != {mint, WSOL}:
        raise RuntimeError(f'{sym}:PAIR_IDENTITY_MISMATCH')
    if not (0 <= d1 <= 18 and 0 <= d2 <= 18):
        raise RuntimeError(f'{sym}:BAD_DECIMALS')
    if (d1 if coin == WSOL else d2) != 9:
        raise RuntimeError(f'{sym}:WSOL_DECIMALS_MISMATCH')
    return {'symbol':sym,'pool':pool,'meme_mint':mint,'owner':owner,'pool_kind':kind,
        'account_len':len(raw), 'coin_mint':coin,'pc_mint':pc,'coin_decimals':d1,'pc_decimals':d2,
        'pool_bytes_sha256':hashlib.sha256(raw).hexdigest(), 'pool_data_b64':value['data'][0], **extra}


def validate_cpmm_config(value):
    if not value or value.get('owner') != CPMM or value.get('executable'):
        raise RuntimeError('CPMM_CONFIG_OWNER')
    raw = base64.b64decode(value['data'][0], validate=True)
    if len(raw) != 236 or raw[:8] != CONFIG_DISCRIMINATOR:
        raise RuntimeError('CPMM_CONFIG_LAYOUT')
    trade, protocol, fund = [int.from_bytes(raw[x:x+8], 'little') for x in (12,20,28)]
    if trade >= 1_000_000 or protocol + fund > 1_000_000:
        raise RuntimeError('CPMM_CONFIG_FEE_RANGE')
    return {'protocol_fee_rate':protocol,'fund_fee_rate':fund,
            'trade_fee_rate_at_preflight':trade, 'cpmm_config_data_b64':value['data'][0],
            'config_bytes_sha256':hashlib.sha256(raw).hexdigest()}


def validate_vault(value, mint, expected_program=TOKEN):
    """Compatibility wrapper; live callers supply the mint's actual program."""
    return int(tc.validate_vault(value, mint, expected_program)['amount_raw'])


def discover_targets(evidence_dir: Path | None = None):
    mints = canonical_mints(); targets = list(ORIGINAL); sources = {}; failures = []
    diag = {'schema':'belial_t3_fix2_pool_diagnostics_v1','identity':'PINNED_MAINNET_MINT',
            'required_pools':12,'scan_started':False,'resolved':{},'failures':failures}
    diag_path = None if evidence_dir is None else Path(evidence_dir) / 'RESOLUTION_SUMMARY.json'
    save_json(diag_path, diag)
    for sym in SYMBOLS[6:]:
        mint = mints[sym]
        print(f'MP12_FIX3_IDENTITY={sym}; mint={mint}; source=PINNED_MAINNET', flush=True)
        try:
            pool, source = resolve_pool(sym, mint, evidence_dir)
            targets.append((sym, pool['id'], mint)); sources[sym] = source
            diag['resolved'][sym] = {'mint':mint,'pool':pool['id'],'onchain_verified':False}
            print(f'MP12_FIX3_POOL_CANDIDATE={sym}; pool={pool["id"]}; RPC_PENDING=true', flush=True)
        except Exception as exc:
            failures.append({'symbol':sym,'mint':mint,'error':str(exc)})
            print(f'MP12_FIX3_POOL_BLOCKED={sym}; reason={exc}', flush=True)
        save_json(diag_path, diag)
    if failures:
        raise RuntimeError('MP12_POOL_DISCOVERY_BLOCKED:' + ';'.join(x['error'] for x in failures))
    if len(targets) != 12 or len({p for _,p,_ in targets}) != 12 or len({m for _,_,m in targets}) != 12:
        raise RuntimeError('DUPLICATE_POOL_OR_MINT')
    return targets, sources, diag


def validate_targets(url, targets, sources, diag, evidence_dir=None, client=None):
    """Inspect ALL accounts before reporting semantic incompatibilities.

    RPC failures still abort immediately: absence of bytes is not a token error.
    Unknown programs/extensions never become an automatic partial-pool session.
    """
    directory = Path(evidence_dir) if evidence_dir is not None else None
    def save(name, value):
        save_json(None if directory is None else directory/name, value)
    failures = []
    matrix = {sym:{'symbol':sym,'pool':pool,'mint':mint,'errors':[],
                   'status':'PENDING','mint_checked':False,'vaults_checked':False}
              for sym,pool,mint in targets}
    def fail(sym, stage, exc):
        item = {'symbol':sym,'stage':stage,'error':str(exc)}
        failures.append(item)
        if sym in matrix:
            matrix[sym]['errors'].append(item)
    accounts, slot = rpc(url, [pool for _,pool,_ in targets], client)
    if len(accounts) != 12:
        raise RuntimeError('RPC_ACCOUNT_COUNT_MISMATCH')
    save('RPC_POOL_ACCOUNTS.json', {'slot':slot,'addresses':[pool for _,pool,_ in targets],'accounts':accounts})
    rows = []
    for target, value in zip(targets, accounts):
        sym = target[0]
        try:
            row = validate_pool(*target, value); rows.append(row)
            matrix[sym].update(pool_kind=row['pool_kind'], pool_owner=row['owner'])
        except Exception as exc:
            fail(sym, 'POOL', exc)
    mint_keys = [mint for _,_,mint in targets] + [WSOL]
    mint_accounts, mint_slot = rpc(url, mint_keys, client)
    if len(mint_accounts) != len(mint_keys):
        raise RuntimeError('RPC_ACCOUNT_COUNT_MISMATCH')
    save('RPC_MINT_ACCOUNTS.json', {'slot':mint_slot,'addresses':mint_keys,'accounts':mint_accounts})
    mint_profiles = {}; mint_values = dict(zip(mint_keys, mint_accounts)); mint_errors = {}
    for key, value in zip(mint_keys, mint_accounts):
        symbol = next((sym for sym,_,mint in targets if mint == key), 'WSOL')
        try:
            profile = tc.validate_mint(value, key); mint_profiles[key] = profile
            print('FIX4_MINT=' + symbol + '; program=' + profile['family'] +
                  '; bytes=' + str(profile['account_len']) + '; extensions=' +
                  ','.join(e['name'] for e in profile['extensions']) + '; STRUCTURE_OK=true', flush=True)
        except Exception as exc:
            mint_errors[key] = str(exc)
            fail(symbol, 'MINT', exc)
            print('FIX4_MINT=' + symbol + '; BLOCKED=' + str(exc), flush=True)
    save('MINT_CAPABILITIES.json', {'revision':tc.REVISION,'slot':mint_slot,
        'profiles':mint_profiles,'errors':mint_errors,'orders':False,'scan_started':False})
    for row in rows:
        sym = row['symbol']
        for which in ('coin','pc'):
            key = row[which + '_mint']; profile = mint_profiles.get(key)
            if profile is None:
                if key == WSOL:
                    fail(sym,'MINT','WSOL_UNAPPROVED:' + mint_errors.get(key,'MISSING'))
                continue
            if profile['decimals'] != row[which + '_decimals']:
                fail(sym,'MINT', 'MINT_DECIMALS_MISMATCH:' + key)
            program = profile['program']
            if key == WSOL and program != TOKEN:
                fail(sym,'MINT','WSOL_PROGRAM_MISMATCH')
            if row['pool_kind'] == 'AMM_V4' and program != TOKEN:
                fail(sym,'PAIR_PROGRAM','LEGACY_AMM_REQUIRES_LEGACY_TOKEN:' + key)
            if row['pool_kind'] == 'CPMM' and row[which + '_token_program'] != program:
                fail(sym,'PAIR_PROGRAM','CPMM_POOL_MINT_PROGRAM_MISMATCH:' + key)
            row[which + '_token_program'] = program
            row[which + '_mint_data_b64'] = mint_values[key]['data'][0]
            row[which + '_mint_capabilities'] = profile
        matrix[sym]['mint_checked'] = True
    modern = [row for row in rows if row['pool_kind'] != 'AMM_V4']
    extra_keys = sorted({key for row in modern for key in (row['coin_vault'],row['pc_vault'])}
                        | {row['amm_config'] for row in modern if row['pool_kind'] == 'CPMM'})
    extra_slot = None
    if extra_keys:
        values, extra_slot = rpc(url, extra_keys, client)
        if len(values) != len(extra_keys):
            raise RuntimeError('RPC_EXTRA_ACCOUNT_COUNT_MISMATCH')
        extra_accounts = dict(zip(extra_keys, values))
        save('RPC_VAULTS_CONFIGS.json', {'slot':extra_slot,'addresses':extra_keys,'accounts':values})
        for row in modern:
            sym = row['symbol']
            for which in ('coin','pc'):
                key = row[which + '_vault']; mint = row[which + '_mint']
                profile = mint_profiles.get(mint)
                if profile is None:
                    fail(sym,'VAULT_DEPENDENCY','UNAPPROVED_MINT:' + mint)
                    continue
                try:
                    vault = tc.validate_vault(extra_accounts[key], mint, profile['program'], key)
                    row[which + '_vault_amount_at_preflight'] = vault['amount_raw']
                    row[which + '_vault_data_b64'] = extra_accounts[key]['data'][0]
                    row[which + '_vault_capabilities'] = vault
                except Exception as exc:
                    fail(sym,'VAULT',exc)
            matrix[sym]['vaults_checked'] = True
            if row['pool_kind'] == 'CPMM':
                try:
                    row.update(validate_cpmm_config(extra_accounts[row['amm_config']]))
                except Exception as exc:
                    fail(sym,'CONFIG',exc)
    for row in rows:
        sym = row['symbol']; source = sources.get(sym)
        if source and source.get('program_id') and source['program_id'] != row['owner']:
            fail(sym,'METADATA',sym + ':METADATA_RPC_PROGRAM_MISMATCH')
        row['token_contract_revision'] = tc.REVISION
    for item in matrix.values():
        item['status'] = 'BLOCKED' if item['errors'] else 'OK'
        print('FIX4_CAPABILITY=' + item['symbol'] + '; status=' + item['status'] +
              '; issues=' + str(len(item['errors'])), flush=True)
    snapshot = {'revision':tc.REVISION,'pools':list(matrix.values()),'failures':failures,
        'ok':not failures,'slot':slot,'mint_slot':mint_slot,'extra_slot':extra_slot,
        'scan_started':False,'orders':False,'required_pools':12,
        'scope':'READ_ONLY_SPOT_SENSOR_NOT_TOKEN_RISK_APPROVAL'}
    save('CAPABILITY_MATRIX.json',snapshot)
    diag.update(onchain_verified_pools=0 if failures else 12, slot=slot, mint_slot=mint_slot,
                ok=not failures, capability_errors=failures)
    save('RESOLUTION_SUMMARY.json',diag)
    if failures:
        raise RuntimeError('MP12_COMPATIBILITY_BLOCKED:' + ';'.join(
            item['symbol'] + '/' + item['stage'] + ':' + item['error'] for item in failures))
    for row in diag['resolved'].values():
        row['onchain_verified'] = True
    save('RESOLUTION_SUMMARY.json',diag)
    manifest = Path(__file__).resolve().parents[1]/'CANONICAL_MINTS.json'
    return {'ok':True,'schema':'belial_t3_pool_manifest_v1','fix':'T3_90M_FIX4_TOKEN_CAPABILITIES',
        'rpc':url,'slot':slot,'mint_slot':mint_slot,'created_ms':time.time_ns()//1000000,
        'pools':rows,'metadata_sources':sources,'identity_source':'PINNED_MAINNET_MINTS',
        'canonical_mints_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
        'token_validation_revision':tc.REVISION,'paper_only':True}


def check(url: str, evidence_dir: Path | None = None) -> dict:
    """Single-provider identity validation, retained for isolated decoder tests."""
    targets, sources, diag = discover_targets(evidence_dir)
    return validate_targets(url, targets, sources, diag, evidence_dir)


def check_auto(evidence_dir, rpc_url=None, ws_url=None):
    import copy
    profiles(rpc_url, ws_url)
    targets, sources, diag = discover_targets(evidence_dir)
    def onchain(url, folder, client):
        return validate_targets(url, targets, sources, copy.deepcopy(diag), folder, client)
    selected=select_rpc(targets, onchain, Path(evidence_dir)/'rpc_fix3', rpc=rpc_url, ws=ws_url)
    selected['fix']='T3_90M_FIX4_TOKEN_CAPABILITIES'
    diag.update(ok=True,onchain_verified_pools=12,selected_rpc_profile=selected['rpc_connection']['profile'],
                slot=selected['slot'],mint_slot=selected['mint_slot'])
    for row in diag['resolved'].values():row['onchain_verified']=True
    save_json(Path(evidence_dir)/'RESOLUTION_SUMMARY.json',diag)
    return selected

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rpc', default=os.environ.get('BELIAL_SOLANA_RPC_URL') or None)
    ap.add_argument('--ws', default=os.environ.get('BELIAL_SOLANA_WS_URL') or None)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args(); args.out.parent.mkdir(parents=True, exist_ok=True)
    evidence_dir = args.out.parent / 'pool_metadata_fix2'
    try:
        report = check_auto(evidence_dir, args.rpc, args.ws)
    except Exception as exc:
        save_json(args.out, {'ok':False,'fix':'T3_90M_FIX4_TOKEN_CAPABILITIES','error':str(exc),
            'scan_started':False,'orders':False,'diagnostics':str(evidence_dir)})
        raise
    save_json(args.out, report)
    print('MP12_POOL_PREFLIGHT_OK=true', flush=True)
    print('POOLS=' + ','.join(row['symbol'] for row in report['pools']), flush=True)


if __name__ == '__main__':
    main()
