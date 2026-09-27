#!/usr/bin/env python3
"""Belial R5D-M1: medicao corrigida e replay isolado; nunca envia transacoes.

Recebe exclusivamente o ZIP R5D encerrado. Nenhum programa do ZIP e executado.
O coletor Rust e a versao R5D congelada nao sao alterados.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import io
import json
import os
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

getcontext().prec = 40
D = Decimal
BPS = D(10000)
ZERO = D(0)
EXPECTED_ZIP = 'a717d47e87ed030a4313df32952ed1f4f062ef6fe679aded0d99dc6e79af1a6c'
SCHEMA = 'belial_r5d_m1_replay_v1'
POLICIES = {
    'TAIL_STOP50_TRAIL35': (D(50), D(50), D(35), 900000),
    'TAIL_STOP75_TRAIL50': (D(75), D(75), D(50), 900000),
    'TAIL_STOP100_TRAIL75': (D(100), D(100), D(75), 1800000),
}
ACTIVE_RULES = frozenset(('SAME_WALLET_10S_GE_5_SOL','SAME_WALLET_10S_GE_3_SELLS',
                         'SAME_WALLET_10S_GE_2_SELLS_GE_2_SOL','MULTI_WALLET_10S_GE_5_SOL'))
ROLES = {'FARTCOIN':'ORIGINAL_REFERENCE','BOME':'SHADOW_COMPARE','WIF':'SHADOW_COMPARE','POPCAT':'SHADOW_COMPARE','RAY':'SHADOW_CONTROL','JUP':'SHADOW_COMPARE','BONK':'SHADOW_COMPARE','PENGU':'SHADOW_COMPARE','PUMP':'SHADOW_COMPARE','USELESS':'SHADOW_COMPARE','GRASS':'SHADOW_COMPARE','PYTH':'SHADOW_COMPARE'}
MAX_MARK_AGE_MS = 30000
# Novo controle tecnico, visivel apenas na variante causal. Nao filtra o pareado.
CAUSAL_SIGNAL_MAX_AGE_MS = 30000
CONFIRM_HORIZON_MS = 5000
ENTRY_LATENCY_MS = 1000
COOLDOWN_MS = 30000
HORIZONS = (500, 1000, 2000, 3000, 5000, 10000, 30000, 60000, 120000, 300000)
COST_SCENARIOS = (20, 50, 75)


def number(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError('Numero ausente ou booleano')
    try:
        value = D(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('Numero invalido') from exc
    if not value.is_finite():
        raise ValueError('Numero nao finito')
    return value


def integer(value: Any) -> int:
    value = number(value)
    if value != value.to_integral_value():
        raise ValueError('Inteiro esperado')
    return int(value)


def short(entry: Any, exit_price: Any) -> Decimal:
    a, b = number(entry), number(exit_price)
    if a <= 0 or b <= 0:
        raise ValueError('Preco deve ser positivo')
    return BPS * (a-b)/a


def serial(value: Any) -> Any:
    # Decimal fica como string exata. Evita apresentar f64 como moeda canonica.
    if isinstance(value, D):
        if not value.is_finite():
            raise ValueError('Decimal nao finito')
        return format(value, 'f')
    if isinstance(value, dict):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    return value


def dumps(value: Any, pretty: bool = False) -> str:
    return json.dumps(serial(value), ensure_ascii=False, sort_keys=True,
                      indent=2 if pretty else None, allow_nan=False)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''):
            h.update(b)
    return h.hexdigest()


def scenario(gross: Any, *, all_in_bps: Any = None,
             entry_fee_bps: Any = None, exit_fee_bps: Any = None,
             exit_entry_ratio: Any = None) -> dict[str, Any]:
    """Modos mutuamente exclusivos: all-in OU taxas por lado, nunca ambos."""
    gross = number(gross)
    if all_in_bps is not None:
        if any(x is not None for x in (entry_fee_bps, exit_fee_bps, exit_entry_ratio)):
            raise ValueError('CUSTOS_DUPLICADOS: all-in e taxas por lado')
        c = number(all_in_bps)
        if c < 0:
            raise ValueError('Custo negativo')
        net = gross-c
        return {'kind': 'ALL_IN_ASSUMED_NOT_MEASURED', 'cost_bps': c,
                'net_bps': net, 'normalized_pnl_per_100': net/100}
    if any(x is None for x in (entry_fee_bps, exit_fee_bps, exit_entry_ratio)):
        raise ValueError('Custo desconhecido nao pode virar zero')
    e, x, r = number(entry_fee_bps)/BPS, number(exit_fee_bps)/BPS, number(exit_entry_ratio)
    if not (0 <= e < 1 and 0 <= x < 1 and r > 0):
        raise ValueError('Taxas/razao invalidas')
    # Mesma quantidade de token vendida e recomprada; limite infinitesimal.
    # Nao ha impacto, emprestimo, funding, gas ou conversao SOL/USD neste benchmark.
    net = BPS*((1-e)-r/(1-x))
    return {'kind': 'FEE_ONLY_INFINITESIMAL_ASSUMPTION',
            'entry_fee_bps': e*BPS, 'exit_fee_bps': x*BPS,
            'cost_bps': gross-net, 'net_bps': net,
            'normalized_pnl_per_100': net/100}


def accounting(entry: Any, exit_price: Any) -> dict[str, Any]:
    a, b = number(entry), number(exit_price)
    g = short(a, b)
    return {'reference_gross_short_bps': g,
            'all_in_scenarios': {str(c): scenario(g, all_in_bps=c) for c in COST_SCENARIOS},
            'fee_only_25bp_each': scenario(g, entry_fee_bps=25, exit_fee_bps=25,
                                          exit_entry_ratio=b/a),
            'executable_net_pnl': None,
            'execution_status': 'NOT_VERIFIED_NO_FILL_OR_BORROW_MODEL',
            'nominal_unit': 'NORMALIZED_100_QUOTE_UNITS_NOT_ACCOUNT_EQUITY',
            'sol_usd_conversion': None}


@dataclass(frozen=True)
class Mark:
    symbol: str
    pool: str
    signature: str
    ts: int
    seq: int
    slot: int
    side: str
    observed: D
    pre: D | None
    post: D
    reference_kind: str = "LOG_PRE_RESERVES_PLUS_OBSERVED_DELTAS"

    @classmethod
    def from_row(cls, r: dict[str, Any], seq: int) -> 'Mark':
        # ray_log registra reservas ANTES do swap. Deltas observados incluem
        # o input efetivamente transferido; nao retirar a taxa uma segunda vez.
        if 'BACKFILL' in str(r.get('source', '')).upper() or r.get('signal_eligible') is False:
            raise ValueError('BACKFILL_SHADOW_ONLY')
        symbol, side = str(r['symbol']), str(r['side'])
        if symbol not in ROLES or side not in ('BUY', 'SELL'):
            raise ValueError('Pool/lado nao reconhecido')
        x, y = number(r['pool_meme_reserve']), number(r['pool_quote_reserve_sol'])
        qty, quote = number(r['meme_qty']), number(r['quote_sol'])
        obs = number(r['price_sol_per_meme'])
        if min(x, y, qty, quote, obs) <= 0:
            raise ValueError('Reserva/quantidade/preco nao positivo')
        if r.get('protocol') is not None:
            from protocol_reference import audit_row
            d = audit_row(r)
            ts = integer(r['received_unix_ms'])
            if ts <= 0 or not str(r['signature']) or not str(r['pool']):
                raise ValueError('Identidade/tempo invalidos')
            return cls(symbol, str(r['pool']), str(r['signature']), ts, seq,
                       integer(r['slot']), side, obs, d['reference_pre'], d['reference_post'],
                       d['reference_basis'])
        if integer(r['log_type']) not in (3, 4):
            raise ValueError('Log nao e swap')
        xp, yp = (x+qty, y-quote) if side == 'SELL' else (x-qty, y+quote)
        if xp <= 0 or yp <= 0:
            raise ValueError('Deltas esgotam a reserva')
        ts = integer(r['received_unix_ms'])
        if ts <= 0 or not str(r['signature']) or not str(r['pool']):
            raise ValueError('Identidade/tempo invalidos')
        return cls(symbol, str(r['pool']), str(r['signature']), ts, seq,
                   integer(r['slot']), side, obs, y/x, yp/xp)

    def meta(self, at: int) -> dict[str, Any]:
        if at < self.ts:
            raise ValueError('LOOKAHEAD')
        return {'swap_line': self.seq, 'signature': self.signature, 'pool': self.pool,
                'slot': self.slot, 'received_unix_ms': self.ts, 'mark_age_ms': at-self.ts,
                'observed_side': self.side, 'observed_swap_price': self.observed,
                'pre_reserve_reference': self.pre, 'post_reserve_reference': self.post,
                'reference_kind': self.reference_kind,
                'canonical_raw_integer_reserves': False, 'executable_quote': False}


class Market:
    def __init__(self, rows: Iterable[dict[str, Any]]):
        self.groups: dict[str, list[Mark]] = defaultdict(list)
        self.by_sig: dict[str, Mark] = {}
        self.pools: dict[str, str] = {}
        self.invalid: list[dict[str, Any]] = []
        for idx, r in enumerate(rows, 1):
            try:
                m = Mark.from_row(r, int(r.get('_line', idx)))
            except (KeyError, ValueError, TypeError) as e:
                self.invalid.append({'line': r.get('_line', idx), 'error': str(e)})
                continue
            if m.signature in self.by_sig:
                raise ValueError('Swap duplicado: '+m.signature)
            if m.symbol in self.pools and self.pools[m.symbol] != m.pool:
                raise ValueError('Nao misturar pools do mesmo simbolo')
            self.pools[m.symbol] = m.pool
            self.by_sig[m.signature] = m
            self.groups[m.symbol].append(m)
        for g in self.groups.values():
            g.sort(key=lambda m: (m.ts, m.seq))
        self.keys = {s: [(m.ts, m.seq) for m in g] for s, g in self.groups.items()}
        self.times = {s: [m.ts for m in g] for s, g in self.groups.items()}
        self.ties = Counter((m.symbol, m.ts) for m in self.by_sig.values())

    def asof(self, symbol: str, at: int, *, seq: int = sys.maxsize,
             max_age: int | None = MAX_MARK_AGE_MS) -> Mark | None:
        keys = self.keys.get(symbol, [])
        i = bisect.bisect_right(keys, (at, seq))-1
        if i < 0:
            return None
        m = self.groups[symbol][i]
        if max_age is not None and at-m.ts > max_age:
            return None
        return m

    def after(self, symbol: str, at: int, end: int) -> Iterable[Mark]:
        g = self.groups.get(symbol, [])
        a = bisect.bisect_right(self.times.get(symbol, []), at)
        b = bisect.bisect_right(self.times.get(symbol, []), end)
        return g[a:b]

    def metadata(self, m: Mark, at: int) -> dict[str, Any]:
        return {**m.meta(at), 'same_ms_observations': self.ties[(m.symbol, m.ts)],
                'tie_order': 'RECORDED_FILE_ORDER_NOT_CHAIN_TRANSACTION_INDEX'}


def absorption_times(exits: list[dict[str, Any]], symbol: str, entry: int) -> list[int]:
    return sorted({integer(r['signal_available_unix_ms']) for r in exits
                   if r['symbol'] == symbol and number(r.get('absorption_score', 0)) >= 60
                   and integer(r['signal_available_unix_ms']) >= entry})


def position(market: Market, episode: dict[str, Any], policy_id: str,
             exits: list[dict[str, Any]], end: int) -> dict[str, Any]:
    """Mesmas politicas; gatilhos direcionais usam referencia, nao ultimo lado.

    Expiracao solicita saida pelo relogio. Sem mark fresco, espera e registra
    atraso, em vez de fingir fill no horario-alvo. Todas as saidas sao referencias.
    """
    entry_t = integer(episode['entry_target_unix_ms'])
    symbol = episode['symbol']
    m0 = market.asof(symbol, entry_t)
    base = {'episode_id': episode['episode_id'], 'policy_id': policy_id,
            'symbol': symbol, 'entry_target_unix_ms': entry_t,
            'trigger_basis': 'POST_RESERVE_DIRECTIONAL_REFERENCE',
            'paper_only': True, 'orders': False, 'transactions_submitted': False,
            'same_entry_as_input_episode': True}
    if m0 is None or entry_t > end:
        return {**base, 'status': 'ENTRY_MARK_UNAVAILABLE', 'accounting': None}
    base['entry_mark'] = market.metadata(m0, entry_t)
    stop, activate, giveback, duration = POLICIES[policy_id]
    due = entry_t+duration
    base['max_hold_due_unix_ms'] = due
    mfe = mae = ZERO
    pending: tuple[int, str] | None = None
    last = m0
    # Market observations precede timers when their recorded milliseconds tie.
    events: list[tuple[int, int, int, str, Mark | None]] = [
        (m.ts, 0, m.seq, 'MARK', m) for m in market.after(symbol, entry_t, end)]
    if due <= end:
        events.append((due, 2, 0, 'MAX_HOLD', None))
    abs_times = absorption_times(exits, symbol, entry_t)
    for t in abs_times[:1]:
        if t <= end:
            events.append((t, 1, 0, 'ABSORPTION_RETURN', None))
    events.sort(key=lambda x: x[:3])

    def close(m: Mark, t: int, why: str, requested: int) -> dict[str, Any]:
        return {**base, 'status': 'REFERENCE_CLOSED_NOT_EXECUTED',
                'exit_reason': why, 'exit_requested_unix_ms': requested,
                'exit_observed_unix_ms': t, 'exit_wait_ms': t-requested,
                'hold_ms': t-entry_t, 'exit_mark': market.metadata(m, t),
                'mfe_reference_bps': mfe, 'mae_reference_bps': mae,
                'accounting': accounting(m0.post, m.post)}

    for t, _, _, kind, m in events:
        if kind == 'MARK':
            assert m is not None
            last = m
            g = short(m0.post, m.post)
            mfe, mae = max(mfe, g), max(mae, -g)
            if pending:
                return close(m, t, pending[1], pending[0])
            if g <= -stop:
                return close(m, t, 'HARD_STOP', t)
            if abs_times and t >= abs_times[0]:
                return close(m, t, 'ABSORPTION_RETURN', abs_times[0])
            if mfe >= activate and mfe-g >= giveback:
                return close(m, t, 'TRAIL_GIVEBACK', t)
        else:
            if pending is None:
                pending = (t, kind)
            m = market.asof(symbol, t)
            if m is not None:
                return close(m, t, pending[1], pending[0])
    mark = market.asof(symbol, end)
    return {**base, 'status': 'EXIT_PENDING_NO_FRESH_MARK' if pending else 'OPEN_END_OF_CAPTURE',
            'exit_requested_unix_ms': pending[0] if pending else None,
            'exit_reason': pending[1] if pending else None,
            'mfe_reference_bps': mfe, 'mae_reference_bps': mae,
            'unrealized_reference_bps': short(m0.post, mark.post) if mark else None,
            'last_mark_age_ms': end-last.ts, 'accounting': None}


def reconcile_legacy(market: Market, trades: list[dict[str, Any]],
                     exits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in trades:
        m0 = market.asof(r['symbol'], integer(r['entry_target_unix_ms']))
        if m0 is None:
            raise ValueError('Preco de entrada legado ausente')
        stop, act, give, duration = POLICIES[r['policy_id']]
        abs_t = absorption_times(exits, r['symbol'], integer(r['entry_target_unix_ms']))
        mfe = mae = ZERO
        answer = None
        a = bisect.bisect_left(market.times[r['symbol']], integer(r['entry_target_unix_ms']))
        for m in market.groups[r['symbol']][a:]:
            g = short(r['entry_price'], m.observed)
            mfe, mae = max(mfe, g), max(mae, -g)
            why = ('HARD_STOP' if g <= -stop else
                   'ABSORPTION_RETURN' if abs_t and m.ts >= abs_t[0] else
                   'TRAIL_GIVEBACK' if mfe >= act and mfe-g >= give else
                   'MAX_HOLD' if m.ts >= integer(r['entry_target_unix_ms'])+duration else None)
            if why:
                answer = m, g, why
                break
        if answer is None:
            raise ValueError('Saida legada nao reproduzivel')
        m, g, why = answer
        errors = {
            'entry': abs(m0.observed-number(r['entry_price'])),
            'exit': abs(m.observed-number(r['exit_price'])),
            'gross_bps': abs(g-number(r['gross_short_bps'])),
            'net_bps': abs(g-number(r['all_in_round_trip_cost_bps'])-number(r['net_short_bps'])),
            'mfe_bps': abs(mfe-number(r['mfe_short_bps'])),
            'mae_bps': abs(mae-number(r['mae_short_bps'])),
            'pnl': abs(number(r['net_short_bps'])/100-number(r['pnl_usd_100'])),
        }
        ok = (why == r['exit_reason'] and m.ts == r['exit_unix_ms']
              and max(errors.values()) < D('0.00000001'))
        if not ok:
            raise ValueError('RECONCILIATION_FAILED '+r['episode_id']+' '+dumps(errors))
        out.append({'episode_id': r['episode_id'], 'policy_id': r['policy_id'],
                    'all_checks_pass': ok, 'numeric_error': errors,
                    'original': r, 'entry_mark': market.metadata(m0, r['entry_target_unix_ms']),
                    'exit_mark': market.metadata(m, m.ts),
                    'same_entry_exit_reference_accounting': accounting(m0.post, m.post),
                    'side_and_price_basis_difference_bps': g-short(m0.post, m.post)})
    return out


def timing(episode: dict[str, Any], arm: dict[str, Any], market: Market) -> dict[str, Any]:
    event = integer(arm['event_unix_ms'])
    avail = integer(arm['arm_available_unix_ms'])
    published = integer(episode['confirm_available_unix_ms'])
    entry = integer(episode['entry_target_unix_ms'])
    if not event <= avail <= published <= entry:
        raise ValueError('Linha do tempo legada inconsistente')
    mark = market.asof(arm['symbol'], entry)
    return {'episode_id': episode['episode_id'], 'event_unix_ms': event,
            'actor_available_unix_ms': avail, 'horizon_5s_due_unix_ms': event+5000,
            'original_confirmation_published_unix_ms': published,
            'entry_target_unix_ms': entry, 'actor_latency_ms': avail-event,
            'signal_age_at_original_confirmation_ms': published-event,
            'horizon_publication_delay_ms': published-(event+5000),
            'signal_age_at_original_entry_ms': entry-event,
            'entry_mark_age_ms': entry-mark.ts if mark else None,
            'signal_older_than_30s': published-event > CAUSAL_SIGNAL_MAX_AGE_MS,
            'fresh_price_does_not_imply_fresh_signal': True}


def causal_shadow(market: Market, arms: list[dict[str, Any]], end: int
                  ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Contrafactual sobre ARM registrados. Nao redata o OOS nem simula RPC.

    O agendador decide no maior entre evento+5s e disponibilidade do ator.
    Nao depende de quando o antigo ledger de aprendizado foi publicado.
    """
    ordered = sorted(arms, key=lambda a: (
        max(integer(a['event_unix_ms'])+5000, integer(a['arm_available_unix_ms'])),
        integer(a.get('_line', 0))))
    decisions, episodes = [], []
    last: dict[str, dict[str, Any]] = {}
    for a in ordered:
        t0, av = integer(a['event_unix_ms']), integer(a['arm_available_unix_ms'])
        if av < t0:
            raise ValueError('Ator disponivel antes do evento')
        due, decision_t = t0+5000, max(t0+5000, av)
        active = a['symbol'] == 'FARTCOIN' and bool(ACTIVE_RULES.intersection(a['rule_ids']))
        # Falha fechada se os ARM nao correspondem ao contrato conhecido.
        if bool(a['active_for_entry']) != active or a['pool_role'] != ROLES[a['symbol']]:
            raise ValueError('ARM mudou de contrato/regra')
        signature = a['event_id'].split('|')[0]
        anchor = market.by_sig.get(signature)
        row = {'event_id': a['event_id'], 'symbol': a['symbol'], 'wallet': a['wallet'],
               'rule_ids': a['rule_ids'], 'event_unix_ms': t0,
               'horizon_due_unix_ms': due, 'actor_available_unix_ms': av,
               'virtual_decision_unix_ms': decision_t,
               'hypothetical_earliest_availability_not_measured_processing_time': True,
               'signal_age_ms': decision_t-t0,
               'horizon_delay_ms': decision_t-due,
               'active_for_entry': active, 'reference_return_5s_bps': None,
               'legacy_absorption_score_at_arm': a['absorption_score_at_arm'],
               'absorption_input_still_from_frozen_collector': True,
               'feedback_to_signal_engine': False}
        reason = None
        m = market.asof(a['symbol'], due)
        if decision_t > end:
            reason = 'HORIZON_NOT_COMPLETE'
        elif anchor is None or anchor.ts != t0 or anchor.symbol != a['symbol']:
            reason = 'TRIGGER_REFERENCE_MISSING'
        elif m is None:
            reason = 'HORIZON_REFERENCE_MISSING_OR_STALE'
        else:
            row['reference_return_5s_bps'] = -short(anchor.post, m.post)
            row['anchor_mark'] = market.metadata(anchor, t0)
            row['horizon_mark'] = market.metadata(m, due)
        if reason is None:
            if not active:
                reason = 'OBSERVE_ONLY_RULE_OR_POOL'
            elif decision_t-t0 > CAUSAL_SIGNAL_MAX_AGE_MS:
                reason = 'SIGNAL_EXPIRED_30S_NEW_QUALITY_GATE'
            elif number(a['absorption_score_at_arm']) >= 60:
                reason = 'ABSORPTION_GATE'
            elif row['reference_return_5s_bps'] >= 0:
                reason = 'NO_NEGATIVE_REFERENCE_CONTINUATION'
            elif a['symbol'] in last and decision_t-last[a['symbol']]['confirm_available_unix_ms'] <= COOLDOWN_MS:
                prior = last[a['symbol']]
                reason = 'EPISODE_SUPPORT_NO_ADDITIONAL_POSITION' if prior.get('opened') else 'COOLDOWN_AFTER_MISSING_ENTRY'
                row['supports_episode_id'] = prior['episode_id'] if prior.get('opened') else None
                prior['supporting_event_ids'].append(a['event_id'])
            else:
                entry_t = decision_t+ENTRY_LATENCY_MS
                mark = market.asof(a['symbol'], entry_t)
                # Reserva cooldown mesmo se a marcacao faltar, como no Tail legado.
                ep_id = f"M1|{a['symbol']}|{a['event_id']}|{decision_t}"
                candidate = {'episode_id': ep_id, 'symbol': a['symbol'], 'wallet': a['wallet'],
                             'arm_event_id': a['event_id'], 'rule_ids': a['rule_ids'],
                             'confirm_available_unix_ms': decision_t,
                             'entry_target_unix_ms': entry_t, 'supporting_event_ids': [], 'opened': False}
                last[a['symbol']] = candidate
                if entry_t > end:
                    reason = 'ENTRY_AFTER_CAPTURE_END'
                elif mark is None:
                    reason = 'ENTRY_MARK_UNAVAILABLE'
                else:
                    reason = 'SHADOW_EPISODE_OPEN'
                    candidate['opened'] = True
                    candidate['entry_mark'] = market.metadata(mark, entry_t)
                    candidate['not_original_oos_entry'] = True
                    episodes.append(candidate)
                    row['new_episode_id'] = ep_id
        row['decision'] = reason
        decisions.append(row)
    return decisions, episodes


class Output:
    """Diretorio novo, JSONL escritos uma vez e state via rename atomico."""
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=False)
        self.bytes_written = 0
        self.closed = False

    def jsonl(self, name: str, rows: Iterable[dict[str, Any]]) -> int:
        total = 0
        with (self.root/name).open('xb') as f:
            for row in rows:
                raw = (dumps(row)+'\n').encode('utf-8')
                f.write(raw)
                self.bytes_written += len(raw)
                total += 1
            f.flush()
            os.fsync(f.fileno())
        return total

    def json(self, name: str, value: Any) -> None:
        raw = (dumps(value, True)+'\n').encode('utf-8')
        dst = self.root/name
        if dst.exists():
            raise FileExistsError(dst)
        tmp = dst.with_name(dst.name+'.tmp')
        with tmp.open('xb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst)
        self.bytes_written += len(raw)


def corrected_labels(market: Market, exits: list[dict[str, Any]],
                     end: int, counts: Counter) -> Iterable[dict[str, Any]]:
    for ex in exits:
        t0, av = integer(ex['event_unix_ms']), integer(ex['signal_available_unix_ms'])
        anchor = market.by_sig.get(ex['event_id'].split('|')[0])
        for h in HORIZONS:
            target, available = t0+h, max(t0+h, av)
            mark = market.asof(ex['symbol'], target)
            status = ('HORIZON_NOT_COMPLETE' if available > end else
                      'TRIGGER_REFERENCE_MISSING' if anchor is None or anchor.ts != t0 else
                      'MARK_MISSING_OR_STALE' if mark is None else 'VALID_REFERENCE_LABEL')
            row = {'schema': 'belial_m1_reference_label_v1', 'event_id': ex['event_id'],
                   'source_exit_line': ex['_line'], 'symbol': ex['symbol'],
                   'horizon_ms': h, 'target_unix_ms': target,
                   'earliest_causal_available_unix_ms': available,
                   'live_publication_time_measured': False, 'status': status,
                   'return_bps': None, 'feedback_to_signal_engine': False,
                   'old_label_overwritten': False}
            if status == 'VALID_REFERENCE_LABEL':
                assert mark is not None and anchor is not None
                row.update(return_bps=-short(anchor.post, mark.post),
                           mark_age_ms=target-mark.ts, anchor_swap_line=anchor.seq,
                           mark_swap_line=mark.seq, mark_signature=mark.signature,
                           boundary_tie_count=market.ties[(mark.symbol, mark.ts)])
            counts[status] += 1
            yield row


def aggregates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for p in POLICIES:
        all_rows = [r for r in rows if r['policy_id'] == p]
        closed = [r for r in all_rows if r['status'] == 'REFERENCE_CLOSED_NOT_EXECUTED']
        costs = {}
        for c in COST_SCENARIOS:
            v = [r['accounting']['all_in_scenarios'][str(c)]['net_bps'] for r in closed]
            gains = sum((x for x in v if x > 0), ZERO)
            losses = -sum((x for x in v if x < 0), ZERO)
            costs[str(c)] = {'assumed_all_in_bps': c, 'closed_count': len(v),
                             'net_bps_sum': sum(v, ZERO),
                             'normalized_pnl_per_100_each': sum(v, ZERO)/100,
                             'wins': sum(x > 0 for x in v),
                             'profit_factor': gains/losses if losses else ('infinite' if gains else None)}
        out[p] = {'input_episodes': len(all_rows), 'reference_closed': len(closed),
                  'unresolved': len(all_rows)-len(closed),
                  'exit_reasons': dict(Counter(r.get('exit_reason') for r in closed)),
                  'gross_reference_bps_sum': sum((r['accounting']['reference_gross_short_bps'] for r in closed), ZERO),
                  'cost_scenarios': costs, 'executable_net_pnl': None}
    return out


def load_archive(path: Path) -> tuple[dict[str, bytes], list[dict[str, Any]], str]:
    digest = sha(path)
    if digest != EXPECTED_ZIP:
        raise ValueError('ZIP_DIFERENTE: esperado '+EXPECTED_ZIP+' obtido '+digest)
    raw, inventory = {}, []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or len(names) > 10000:
            raise ValueError('ZIP duplicado/excessivo')
        if sum(i.file_size for i in z.infolist()) > 500_000_000:
            raise ValueError('ZIP excede limite de leitura')
        roots = {PurePosixPath(n).parts[0] for n in names}
        if len(roots) != 1:
            raise ValueError('ZIP sem raiz unica')
        root = next(iter(roots))
        for i in z.infolist():
            p = PurePosixPath(i.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in i.filename or i.is_dir():
                raise ValueError('Caminho ZIP invalido')
            rel = str(p.relative_to(root))
            b = z.read(i)  # leitura verifica CRC; nenhuma extracao executavel.
            raw[rel] = b
            inventory.append({'member': rel, 'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()})
    if 'SESSION_COMPLETE' not in raw:
        raise ValueError('Sessao ainda nao encerrada')
    return raw, inventory, digest


def parse_lines(b: bytes) -> list[dict[str, Any]]:
    out = []
    for i, line in enumerate(b.splitlines(), 1):
        if not line.strip():
            continue
        r = json.loads(line, parse_float=D, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        if not isinstance(r, dict):
            raise ValueError('JSONL nao contem objeto')
        r['_line'] = i
        out.append(r)
    return out


def run(archive: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError('Destino deve ser NOVO: '+str(output))
    raw, inventory, digest = load_archive(archive)
    sw = parse_lines(raw['swaps.jsonl'])
    ex = parse_lines(raw['exits.jsonl'])
    ev = parse_lines(raw['r5d_tail/tail_events.jsonl'])
    tr = parse_lines(raw['r5d_tail/tail_trades.jsonl'])
    arms = [r for r in ev if r['kind'] == 'ARM']
    eps = [r for r in ev if r['kind'] == 'EPISODE_OPEN']
    for rows, key in ((ex, lambda r:r['event_id']), (arms, lambda r:r['event_id']),
                      (eps, lambda r:r['episode_id']), (tr, lambda r:(r['episode_id'],r['policy_id']))):
        keys = [key(r) for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError('Chaves duplicadas no OOS')
    armap = {a['event_id']:a for a in arms}
    manifest = json.loads(raw['FROZEN_RUN_MANIFEST.json'])
    if manifest['paper_only'] is not True or manifest['orders'] is not False or manifest['transactions_submitted'] is not False:
        raise ValueError('SAFETY_GATE')
    end = max(integer(r['unix_ms']) for r in parse_lines(raw['supervisor_events.jsonl']))
    market = Market(sw)
    if market.invalid:
        raise ValueError('Swap invalido na base validada: '+dumps(market.invalid[:3]))
    legacy = reconcile_legacy(market, tr, ex)
    measured = [position(market, e, p, ex, end) for e in eps for p in POLICIES]
    # Pareamento explicito por ID e politica; nunca por numero de linha/ordem.
    lm = {(r['episode_id'],r['policy_id']):r for r in legacy}
    for r in measured:
        old = lm[(r['episode_id'], r['policy_id'])]
        original = old['original']
        r['original_exit_unix_ms'] = original['exit_unix_ms']
        r['original_exit_reason'] = original['exit_reason']
        r['changes'] = ['POST_RESERVE_REFERENCE_NOT_MIXED_SWAP_PRICE', 'COSTS_SEPARATE_FROM_DIRECTION']
        if r.get('exit_observed_unix_ms') != original['exit_unix_ms']:
            r['changes'].append('EXIT_TIME_CHANGED_BY_REFERENCE_OR_CLOCK')
        if r.get('exit_reason') != original['exit_reason']:
            r['changes'].append('EXIT_REASON_CHANGED')
    times = [timing(e, armap[e['arm_event_id']], market) for e in eps]
    decisions, new_eps = causal_shadow(market, arms, end)
    new_trades = [position(market, e, p, ex, end) for e in new_eps for p in POLICIES]
    original_stats = {p:{'n':sum(r['policy_id']==p for r in tr),
                         'normalized_pnl_per_100_each':sum((number(r['pnl_usd_100']) for r in tr if r['policy_id']==p), ZERO)} for p in POLICIES}
    legacy_stops = [r for r in legacy if r['policy_id']=='TAIL_STOP50_TRAIL35' and r['original']['exit_reason']=='HARD_STOP']
    changed_stop_count = sum(next(x for x in measured if x['episode_id']==r['episode_id'] and x['policy_id']==r['policy_id']).get('exit_reason') != 'HARD_STOP' for r in legacy_stops)
    summary = {'schema':SCHEMA, 'version':'R5D-M1', 'mode':'COMPLETED_CAPTURE_REPLAY_NOT_NEW_OOS',
               'input_sha256':digest, 'session':manifest['session'], 'input_manifest':manifest,
               'paper_only':True, 'orders':False, 'transactions_submitted':False,
               'signal_engine_feedback':False, 'originals_modified':False,
               'source_swaps':len(sw), 'source_exits':len(ex), 'source_arms':len(arms),
               'original_episodes':len(eps), 'legacy_trades_reconciled':len(legacy),
               'all_legacy_trades_match':all(x['all_checks_pass'] for x in legacy),
               'paired_rows':len(measured), 'paired_same_episode_ids':True,
               'original':original_stats, 'paired_reference':aggregates(measured),
               'old_stop50_hard_stops':len(legacy_stops),
               'old_stop50_hard_stops_no_longer_hard_stop':changed_stop_count,
               'causal_shadow':{'recorded_arms_only':True, 'not_validated_new_oos':True,
                                'new_signal_age_quality_gate_ms':CAUSAL_SIGNAL_MAX_AGE_MS,
                                'gate_not_fitted_to_pnl':True,
                                'decisions':dict(Counter(r['decision'] for r in decisions)),
                                'episodes':len(new_eps), 'policies':aggregates(new_trades)},
               'timing':{'original_confirmations_over_30s':sum(r['signal_older_than_30s'] for r in times),
                         'max_original_signal_age_ms':max((r['signal_age_at_original_confirmation_ms'] for r in times), default=None)},
               'remaining_limits':['NO_ACTUAL_SHORT_BORROW_OR_FILLS', 'NO_HISTORICAL_POOL_FEE_VERIFICATION',
                                   'NO_SOL_USD_CONVERSION_OR_SIZE_IMPACT', 'RESERVE_INPUTS_ALREADY_FLOAT_NORMALIZED_IN_SOURCE',
                                   'RECORDED_MS_ORDER_NOT_CANONICAL_CHAIN_ORDER',
                                   'FROZEN_COLLECTOR_ABSORPTION_REMAINS_LEGACY',
                                   'NO_CORRECTION_TO_OLD_RUST_CHECKPOINT_WRITER'],
               'promotion_to_live':False, 'thresholds_changed_by_optimization':False}
    out = Output(output)
    out.json('input_inventory.json', inventory)
    out.json('legacy_reconciliation.json', legacy)
    out.jsonl('paired_trades.jsonl', measured)
    out.jsonl('original_episode_timing.jsonl', times)
    out.jsonl('causal_shadow_decisions.jsonl', decisions)
    out.jsonl('causal_shadow_episodes.jsonl', new_eps)
    out.jsonl('causal_shadow_trades.jsonl', new_trades)
    counts = Counter()
    label_count = out.jsonl('corrected_learning_labels.jsonl', corrected_labels(market, ex, end, counts))
    summary['corrected_labels'] = {'rows':label_count, 'statuses':dict(counts),
                                   'missing_labels_are_null_not_zero':True, 'trained_model':False}
    summary['output_data_bytes_written_before_summary'] = out.bytes_written
    summary['write_policy'] = 'ONCE_PER_OUTPUT_JSONL_NO_REWRITE_LOOP'
    out.json('summary.json', summary)
    # O original e verificado novamente apos o replay.
    if sha(archive) != digest:
        raise ValueError('SOURCE_CHANGED_DURING_REPLAY')
    with (output/'COMPLETE').open('x', encoding='utf-8') as f:
        f.write('R5D_M1_REPLAY_COMPLETE=true\nINPUT_UNCHANGED=true\n')
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--archive', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    summary = run(args.archive.resolve(), args.output.resolve())
    print(dumps({'R5D_M1_REPLAY_OK':True, 'legacy_trades_reconciled':summary['legacy_trades_reconciled'],
                 'paired_rows':summary['paired_rows'], 'output':str(args.output.resolve()),
                 'new_oos_started':False, 'executable_net_pnl':None}, True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
        print('R5D_M1_FAILED='+str(exc), file=sys.stderr)
        raise SystemExit(2)
