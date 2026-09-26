//! FIX4: explicit Raydium contracts with validated SPL/Token-2022 evidence.
//! No signing, transaction submission, virtual-reserve substitution or price API.
use super::*;
pub const CPMM: &str = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C";
pub const CLMM: &str = "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK";

// SHA256("event:SwapEvent")[0..8] / SHA256("account:PoolState")[0..8].
pub const SWAP_DISC: [u8; 8] = [64, 198, 205, 232, 38, 8, 113, 226];
const POOL_DISC: [u8; 8] = [247, 237, 227, 245, 215, 195, 222, 70];

fn key(data: &[u8], offset: usize) -> Result<String, String> {
    Ok(bs58::encode(data.get(offset..offset + 32).ok_or("short_pubkey")?).into_string())
}
fn u128_le(data: &[u8], offset: usize) -> Result<u128, String> {
    let mut buf = [0u8; 16];
    buf.copy_from_slice(data.get(offset..offset + 16).ok_or("short_u128")?);
    Ok(u128::from_le_bytes(buf))
}
fn boolean(data: &[u8], offset: usize) -> Result<bool, String> {
    match data.get(offset) {
        Some(0) => Ok(false),
        Some(1) => Ok(true),
        _ => Err("invalid_bool".into()),
    }
}
fn positive(v: f64) -> Result<f64, String> {
    if v.is_finite() && v > 0.0 {
        Ok(v)
    } else {
        Err("nonpositive_nonfinite".into())
    }
}

pub fn meta(cfg: &PoolCfg, owner: &str, data: &[u8]) -> Result<PoolMeta, String> {
    let (c, p, d0, d1, v0, v1, config) = match owner {
        CPMM => {
            if data.len() != 637 || data[..8] != POOL_DISC {
                return Err("CPMM_POOL_LAYOUT".into());
            }
            if key(data, 232)? != token_programs::declared(&cfg.coin_token_program)
                || key(data, 264)? != token_programs::declared(&cfg.pc_token_program)
                || !token_programs::supported(token_programs::declared(&cfg.coin_token_program))
                || !token_programs::supported(token_programs::declared(&cfg.pc_token_program))
            {
                return Err("CPMM_TOKEN_PROGRAM".into());
            }
            if data[329] & 4 != 0 {
                return Err("CPMM_SWAPS_DISABLED".into());
            }
            (
                key(data, 168)?,
                key(data, 200)?,
                data[331] as u32,
                data[332] as u32,
                key(data, 72)?,
                key(data, 104)?,
                key(data, 8)?,
            )
        }
        CLMM => {
            if data.len() != 1544 || data[..8] != POOL_DISC {
                return Err("CLMM_POOL_LAYOUT".into());
            }
            if data[389] & 16 != 0 || u128_le(data, 253)? == 0 {
                return Err("CLMM_DISABLED_OR_PRICE".into());
            }
            (
                key(data, 73)?,
                key(data, 105)?,
                data[233] as u32,
                data[234] as u32,
                key(data, 137)?,
                key(data, 169)?,
                key(data, 9)?,
            )
        }
        _ => return Err("UNSUPPORTED_PROTOCOL".into()),
    };
    if cfg.owner != owner
        || !((c == cfg.meme_mint && p == WSOL) || (p == cfg.meme_mint && c == WSOL))
    {
        return Err("PROTOCOL_PAIR_IDENTITY".into());
    }
    if d0 > 18 || d1 > 18 || (if c == WSOL { d0 } else { d1 }) != 9 {
        return Err("PROTOCOL_DECIMALS".into());
    }
    if cfg.coin_vault != v0 || cfg.pc_vault != v1 || cfg.amm_config != config || v0 == v1 {
        return Err("PROTOCOL_VAULT_CONFIG_CHANGED".into());
    }
    if owner == CPMM
        && (cfg.cpmm_config_data_b64.is_empty()
            || cfg.protocol_fee_rate > 1_000_000
            || cfg.fund_fee_rate > 1_000_000 - cfg.protocol_fee_rate)
    {
        return Err("CPMM_CONFIG_REQUIRED".into());
    }
    if owner == CPMM {
        let config = STANDARD
            .decode(&cfg.cpmm_config_data_b64)
            .map_err(|_| "CPMM_CONFIG_BASE64")?;
        const CONFIG_DISC: [u8; 8] = [218, 244, 33, 104, 203, 203, 43, 111];
        if config.len() != 236
            || config[..8] != CONFIG_DISC
            || u64_le(&config, 20)? != cfg.protocol_fee_rate
            || u64_le(&config, 28)? != cfg.fund_fee_rate
        {
            return Err("CPMM_CONFIG_FIELDS_DISAGREE".into());
        }
    }
    let meta = PoolMeta {
        coin_is_meme: c == cfg.meme_mint,
        coin_mint: c,
        pc_mint: p,
        coin_decimals: d0,
        pc_decimals: d1,
    };
    token_programs::verify_cfg(cfg, &meta)?;
    Ok(meta)
}

/// Only committed CPI frames. An inner invocation cannot spoof its parent's logs.
/// Pool-address matching is explicit in Anchor events; legacy ray_log still has
/// the inherited one-swap-per-transaction quarantine.
pub fn payloads(logs: &[Value], owner: &str, pool: &str) -> Result<Vec<String>, String> {
    let mut stack: Vec<(String, Vec<String>)> = Vec::new();
    let mut committed = Vec::new();
    for row in logs {
        let line = row.as_str().ok_or("LOG_NOT_STRING")?;
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() == 4 && parts[0] == "Program" && parts[2] == "invoke" {
            let depth = parts[3]
                .trim_matches(&['[', ']'][..])
                .parse::<usize>()
                .map_err(|_| "LOG_DEPTH")?;
            if depth != stack.len() + 1 {
                return Err("LOG_STACK_DISCONTINUITY".into());
            }
            stack.push((parts[1].to_owned(), Vec::new()));
        } else if parts.len() >= 3
            && parts[0] == "Program"
            && (parts[2] == "success" || parts[2].starts_with("failed"))
        {
            let frame = stack.pop().ok_or("LOG_STACK_EMPTY")?;
            if frame.0 != parts[1] {
                return Err("LOG_STACK_MISMATCH".into());
            }
            if parts[2] == "success" {
                if let Some(parent) = stack.last_mut() {
                    parent.1.extend(frame.1);
                } else {
                    committed.extend(frame.1);
                }
            }
        } else if stack.last().is_some_and(|f| f.0 == owner) {
            if owner == RAYDIUM_AMM_V4 {
                if let Some(encoded) = line.strip_prefix("Program log: ray_log: ") {
                    let raw = STANDARD.decode(encoded.trim()).map_err(|_| "LOG_BASE64")?;
                    if matches!(raw.first(), Some(3) | Some(4)) {
                        stack.last_mut().unwrap().1.push(encoded.trim().to_owned());
                    }
                }
            } else if let Some(encoded) = line.strip_prefix("Program data: ") {
                let raw = STANDARD.decode(encoded.trim()).map_err(|_| "LOG_BASE64")?;
                if raw.len() >= 40 && raw[..8] == SWAP_DISC && key(&raw, 8)? == pool {
                    stack.last_mut().unwrap().1.push(encoded.trim().to_owned());
                }
            }
        }
    }
    if !stack.is_empty() {
        return Err("TRUNCATED_LOG_STACK".into());
    }
    Ok(committed)
}

fn vault_amount(
    tx: &Value,
    cfg: &PoolCfg,
    meta: &PoolMeta,
    which: bool,
    pre: bool,
) -> Result<u64, String> {
    let keys = account_keys(tx);
    let (vault, mint, dec) = if which {
        (&cfg.coin_vault, &meta.coin_mint, meta.coin_decimals)
    } else {
        (&cfg.pc_vault, &meta.pc_mint, meta.pc_decimals)
    };
    let indexes: Vec<usize> = keys
        .iter()
        .enumerate()
        .filter_map(|(i, k)| if k == vault { Some(i) } else { None })
        .collect();
    if indexes.len() != 1 {
        return Err("VAULT_KEY_AMBIGUOUS".into());
    }
    let rows = tx
        .pointer(if pre {
            "/meta/preTokenBalances"
        } else {
            "/meta/postTokenBalances"
        })
        .and_then(Value::as_array)
        .ok_or("VAULT_BALANCES_MISSING")?;
    let found: Vec<&Value> = rows
        .iter()
        .filter(|r| r["accountIndex"].as_u64() == Some(indexes[0] as u64))
        .collect();
    if found.len() != 1 {
        return Err("VAULT_BALANCE_AMBIGUOUS".into());
    }
    let r = found[0];
    if r["mint"].as_str() != Some(mint.as_str())
        || r["uiTokenAmount"]["decimals"].as_u64() != Some(dec as u64)
    {
        return Err("VAULT_IDENTITY".into());
    }
    if let Some(program) = r.get("programId") {
        let expected = if which {
            &cfg.coin_token_program
        } else {
            &cfg.pc_token_program
        };
        if program.as_str() != Some(token_programs::declared(expected)) {
            return Err("VAULT_BALANCE_PROGRAM_MISMATCH".into());
        }
    }
    r["uiTokenAmount"]["amount"]
        .as_str()
        .ok_or("VAULT_AMOUNT_MISSING")?
        .parse::<u64>()
        .map_err(|_| "VAULT_AMOUNT_RANGE".into())
}

pub fn decode(
    encoded: &str,
    cfg: &PoolCfg,
    meta: &PoolMeta,
    signature: &str,
    slot: u64,
    received: u64,
    received_ns: u64,
    tx: Option<&Value>,
) -> Result<SwapEvent, String> {
    let raw = STANDARD.decode(encoded).map_err(|_| "EVENT_BASE64")?;
    if raw.len() < 40 || raw[..8] != SWAP_DISC || key(&raw, 8)? != cfg.pool {
        return Err("SWAP_IDENTITY".into());
    }
    let metadata = json!({"owner":cfg.owner,"pool":cfg.pool,"symbol":cfg.symbol,"meme_mint":cfg.meme_mint,
        "coin_mint":meta.coin_mint,"pc_mint":meta.pc_mint,"coin_decimals":meta.coin_decimals,"pc_decimals":meta.pc_decimals,
        "coin_vault":cfg.coin_vault,"pc_vault":cfg.pc_vault,"amm_config":cfg.amm_config,
        "coin_token_program":token_programs::declared(&cfg.coin_token_program),"pc_token_program":token_programs::declared(&cfg.pc_token_program),
        "token_contract_revision":cfg.token_contract_revision,
        "protocol_fee_rate":cfg.protocol_fee_rate,"fund_fee_rate":cfg.fund_fee_rate,"cpmm_config_data_b64":cfg.cpmm_config_data_b64});
    let mut evidence = json!({"event_b64":encoded,"metadata":metadata,"pool_kind":if cfg.owner==CPMM{"CPMM"}else{"CLMM"}});
    let (
        side,
        meme_raw,
        quote_raw,
        meme_before,
        quote_before,
        post_price,
        tag,
        direction,
        actor_hint,
    ) = if cfg.owner == CPMM {
        if raw.len() != 170 {
            return Err(format!("CPMM_EVENT_LAYOUT:{}", raw.len()));
        }
        let ib = u64_le(&raw, 40)?;
        let ob = u64_le(&raw, 48)?;
        let ia = u64_le(&raw, 56)?;
        let oa = u64_le(&raw, 64)?;
        if u64_le(&raw, 72)? != 0 || u64_le(&raw, 80)? != 0 {
            return Err("TRANSFER_FEE_UNSUPPORTED".into());
        }
        let _base_input = boolean(&raw, 88)?;
        let im = key(&raw, 89)?;
        let om = key(&raw, 121)?;
        if !((im == cfg.meme_mint && om == WSOL) || (im == WSOL && om == cfg.meme_mint)) {
            return Err("CPMM_EVENT_MINTS".into());
        }
        let fee = u64_le(&raw, 153)?;
        let creator = u64_le(&raw, 161)?;
        let creator_input = boolean(&raw, 169)?;
        if ib == 0 || ob == 0 || ia == 0 || oa == 0 || fee >= ia {
            return Err("CPMM_AMOUNTS".into());
        }
        let pr = cfg.protocol_fee_rate;
        let fr = cfg.fund_fee_rate;
        if pr > 1_000_000 || fr > 1_000_000 - pr {
            return Err("CPMM_FEE_RATES".into());
        }
        let proto = (fee as u128 * pr as u128 / 1_000_000) as u64;
        let fund = (fee as u128 * fr as u128 / 1_000_000) as u64;
        let ip = (ib as u128 + ia as u128)
            .checked_sub(
                proto as u128 + fund as u128 + if creator_input { creator as u128 } else { 0 },
            )
            .ok_or("CPMM_INPUT_DEPLETED")?;
        let op = ob
            .checked_sub(oa)
            .and_then(|v| v.checked_sub(if creator_input { 0 } else { creator }))
            .ok_or("CPMM_OUTPUT_DEPLETED")?;
        if ip == 0 || ip > u64::MAX as u128 || op == 0 {
            return Err("CPMM_RESERVE_RANGE".into());
        }
        let sell = im == cfg.meme_mint;
        let (qm, qq, bm, bq, pm, pq) = if sell {
            (ia, oa, ib, ob, ip as u64, op)
        } else {
            (oa, ia, ob, ib, op, ip as u64)
        };
        let md = if meta.coin_is_meme {
            meta.coin_decimals
        } else {
            meta.pc_decimals
        };
        evidence["reference_basis"] = json!("CPMM_POST_LP_RESERVES_FROZEN_CONFIG_NOT_EXECUTION");
        evidence["fee_config_basis"] = json!("ONCHAIN_PREFLIGHT_FROZEN_PARAMETERS");
        evidence["pre_meme_raw"] = json!(bm.to_string());
        evidence["pre_quote_raw"] = json!(bq.to_string());
        evidence["post_meme_raw"] = json!(pm.to_string());
        evidence["post_quote_raw"] = json!(pq.to_string());
        (
            if sell { Side::Sell } else { Side::Buy },
            qm,
            qq,
            bm,
            bq,
            amount_ui(pq, 9) / amount_ui(pm, md),
            5,
            if im == meta.coin_mint { 2 } else { 1 },
            None,
        )
    } else if cfg.owner == CLMM {
        if raw.len() != 205 && raw.len() != 221 {
            return Err(format!("CLMM_EVENT_LAYOUT:{}", raw.len()));
        }
        let a0 = u64_le(&raw, 136)?;
        let a1 = u64_le(&raw, 152)?;
        if u64_le(&raw, 144)? != 0 || u64_le(&raw, 160)? != 0 {
            return Err("TRANSFER_FEE_UNSUPPORTED".into());
        }
        let z = boolean(&raw, 168)?;
        let sqrt = u128_le(&raw, 169)?;
        let liq = u128_le(&raw, 185)?;
        let tick = i32::from_le_bytes(raw[201..205].try_into().map_err(|_| "TICK_BYTES")?);
        if a0 == 0 || a1 == 0 || sqrt == 0 || !(-443636..=443636).contains(&tick) {
            return Err("CLMM_AMOUNTS_SQRT_TICK".into());
        }
        let normalized = sqrt as f64 / 18_446_744_073_709_551_616.0;
        let tick_float = 2.0 * normalized.ln() / 1.0001_f64.ln();
        if !tick_float.is_finite() || (tick_float - tick as f64).abs() > 2.00001 {
            return Err("CLMM_SQRT_TICK_INCONSISTENT".into());
        }
        let price = normalized
            * normalized
            * 10_f64.powi(meta.coin_decimals as i32 - meta.pc_decimals as i32);
        let tx = tx.ok_or("CLMM_TX_REQUIRED_FOR_REAL_VAULTS")?;
        if !tx.pointer("/meta/err").is_some_and(Value::is_null)
            || tx["slot"].as_u64() != Some(slot)
            || !tx
                .pointer("/transaction/signatures")
                .and_then(Value::as_array)
                .is_some_and(|s| s.iter().any(|s| s.as_str() == Some(signature)))
        {
            return Err("CLMM_TX_IDENTITY_OR_FAILURE".into());
        }
        // Verify the exact successful event is also in the RPC transaction.
        let txlogs = tx
            .pointer("/meta/logMessages")
            .and_then(Value::as_array)
            .ok_or("CLMM_TX_LOGS")?;
        let matches = payloads(txlogs, CLMM, &cfg.pool)?;
        if matches.len() != 1 || matches[0] != encoded {
            return Err("CLMM_TX_EVENT_MISMATCH_OR_MULTI_SWAP".into());
        }
        let pre0 = vault_amount(tx, cfg, meta, true, true)?;
        let pre1 = vault_amount(tx, cfg, meta, false, true)?;
        let post0 = vault_amount(tx, cfg, meta, true, false)?;
        let post1 = vault_amount(tx, cfg, meta, false, false)?;
        let deltas = (post0 as i128 - pre0 as i128, post1 as i128 - pre1 as i128);
        let expected = if z {
            (a0 as i128, -(a1 as i128))
        } else {
            (-(a0 as i128), a1 as i128)
        };
        if deltas != expected || [pre0, pre1, post0, post1].contains(&0) {
            return Err("CLMM_TX_VAULT_DELTA_MISMATCH_OR_MULTI_OPERATION".into());
        }
        let (qm, qq, bm, bq, pm, pq) = if meta.coin_is_meme {
            (a0, a1, pre0, pre1, post0, post1)
        } else {
            (a1, a0, pre1, pre0, post1, post0)
        };
        evidence["reference_basis"] = json!("CLMM_SQRT_PRICE_X64_NOT_VAULT_RATIO_NOT_EXECUTION");
        evidence["reserve_basis"] = json!("TRANSACTION_PRE_VAULT_BALANCES_NOT_ACTIVE_LIQUIDITY");
        evidence["sqrt_price_x64"] = json!(sqrt.to_string());
        evidence["liquidity"] = json!(liq.to_string());
        evidence["tick"] = json!(tick);
        evidence["pre_meme_raw"] = json!(bm.to_string());
        evidence["pre_quote_raw"] = json!(bq.to_string());
        evidence["post_meme_raw"] = json!(pm.to_string());
        evidence["post_quote_raw"] = json!(pq.to_string());
        evidence["vaults_verified_with_same_transaction"] = json!(true);
        evidence["transaction_evidence"] = json!({"slot":tx["slot"],
            "transaction":{"signatures":tx["transaction"]["signatures"],"message":tx["transaction"]["message"]},
            "meta":{"err":tx["meta"]["err"],"logMessages":tx["meta"]["logMessages"],
                "preTokenBalances":tx["meta"]["preTokenBalances"],"postTokenBalances":tx["meta"]["postTokenBalances"],
                "loadedAddresses":tx["meta"]["loadedAddresses"],"innerInstructions":tx["meta"]["innerInstructions"]}});
        (
            if z == meta.coin_is_meme {
                Side::Sell
            } else {
                Side::Buy
            },
            qm,
            qq,
            bm,
            bq,
            if meta.coin_is_meme {
                price
            } else {
                1.0 / price
            },
            6,
            if z { 2 } else { 1 },
            Some(parse_actor(tx, &cfg.pool)),
        )
    } else {
        return Err("UNSUPPORTED_PROTOCOL".into());
    };
    let md = if meta.coin_is_meme {
        meta.coin_decimals
    } else {
        meta.pc_decimals
    };
    let mq = positive(amount_ui(meme_raw, md))?;
    let quote = positive(amount_ui(quote_raw, 9))?;
    let refprice = positive(post_price)?;
    evidence["reference_post"] = json!(refprice);
    Ok(SwapEvent {
        symbol: cfg.symbol.clone(),
        pool: cfg.pool.clone(),
        signature: signature.to_owned(),
        slot,
        received_unix_ms: received,
        received_unix_ns: received_ns,
        log_type: tag,
        direction,
        side,
        price_sol_per_meme: positive(quote / mq)?,
        meme_qty: mq,
        quote_sol: quote,
        pool_meme_reserve: positive(amount_ui(meme_before, md))?,
        pool_quote_reserve_sol: positive(amount_ui(quote_before, 9))?,
        protocol: Some(evidence),
        actor_hint,
    })
}

pub async fn fetch_tx(client: &Client, rpc: &str, signature: &str) -> Result<Value, String> {
    let mut last = String::new();
    for attempt in 0..3 {
        let result=client.post(rpc).timeout(Duration::from_secs(5)).json(&json!({"jsonrpc":"2.0","id":1,
            "method":"getTransaction","params":[signature,{"encoding":"json","commitment":"confirmed","maxSupportedTransactionVersion":0}]})).send().await;
        match result {
            Ok(r) => match r.error_for_status() {
                Ok(r) => match r.json::<Value>().await {
                    Ok(v) => {
                        if let Some(tx) = v.get("result").filter(|x| !x.is_null()) {
                            return Ok(tx.clone());
                        }
                        last = v.to_string();
                    }
                    Err(e) => last = e.to_string(),
                },
                Err(e) => last = e.to_string(),
            },
            Err(e) => last = e.to_string(),
        }
        if attempt < 2 {
            tokio::time::sleep(Duration::from_millis(200 * (attempt + 1))).await;
        }
    }
    Err(format!("CLMM_TX_UNAVAILABLE:{last}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn cases() -> Vec<Value> {
        include_str!("../tests/protocol_cases.jsonl")
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }
    fn cfg_meta(v: &Value) -> (PoolCfg, PoolMeta) {
        let cfg: PoolCfg = serde_json::from_value(v["cfg"].clone()).unwrap();
        let data = STANDARD
            .decode(v["pool_data_b64"].as_str().unwrap())
            .unwrap();
        let m = meta(&cfg, &cfg.owner, &data).unwrap();
        (cfg, m)
    }
    fn event(v: &Value) -> Result<SwapEvent, String> {
        let (c, m) = cfg_meta(v);
        decode(
            v["event_b64"].as_str().unwrap(),
            &c,
            &m,
            v["signature"].as_str().unwrap(),
            v["slot"].as_u64().unwrap(),
            1790000000000,
            1790000000000000000,
            v.get("tx"),
        )
    }
    fn cp() -> Value {
        cases()
            .into_iter()
            .find(|c| c["cfg"]["owner"] == CPMM)
            .unwrap()
    }
    fn cl() -> Value {
        cases()
            .into_iter()
            .find(|c| c["cfg"]["owner"] == CLMM)
            .unwrap()
    }
    fn mutate(v: &mut Value, off: usize, data: &[u8]) {
        let mut raw = STANDARD.decode(v["event_b64"].as_str().unwrap()).unwrap();
        raw[off..off + data.len()].copy_from_slice(data);
        v["event_b64"] = json!(STANDARD.encode(raw));
    }
    #[test]
    fn golden_cpmm_clmm_side_orientation_fees_and_reference() {
        let rows = cases();
        assert_eq!(rows.len(), 24);
        for v in rows {
            let e = event(&v).unwrap();
            let want = &v["expected"];
            assert_eq!(e.side.label(), want["side"].as_str().unwrap());
            assert_eq!(e.log_type as u64, want["log_type"].as_u64().unwrap());
            assert_eq!(e.direction, want["direction"].as_u64().unwrap());
            let px: f64 = want["reference_post"].as_str().unwrap().parse().unwrap();
            assert!((e.reference_post().unwrap() - px).abs() <= px.abs() * 1e-12);
            for name in [
                "pre_meme_raw",
                "pre_quote_raw",
                "post_meme_raw",
                "post_quote_raw",
            ] {
                let got = e.protocol.as_ref().unwrap()[name]
                    .as_str()
                    .unwrap()
                    .parse::<u64>()
                    .unwrap();
                assert_eq!(got, want[name].as_u64().unwrap());
            }
        }
    }
    #[test]
    fn cpmm_trade_fee_lp_component_is_not_removed_twice() {
        let e = event(&cp()).unwrap();
        assert_eq!(e.protocol.unwrap()["post_meme_raw"], "1000999100000");
    }
    #[test]
    fn clmm_uses_sqrt_not_custody_ratio() {
        let e = event(&cl()).unwrap();
        assert!(
            (e.reference_post().unwrap() - e.pool_quote_reserve_sol / e.pool_meme_reserve).abs()
                > 1e-8
        );
    }
    #[test]
    fn cpmm_bad_bool_rejected() {
        let mut v = cp();
        mutate(&mut v, 169, &[2]);
        assert!(event(&v).unwrap_err().contains("bool"));
    }
    #[test]
    fn clmm_bad_bool_rejected() {
        let mut v = cl();
        mutate(&mut v, 168, &[2]);
        assert!(event(&v).unwrap_err().contains("bool"));
    }
    #[test]
    fn cpmm_transfer_fee_rejected() {
        let mut v = cp();
        mutate(&mut v, 72, &1_u64.to_le_bytes());
        assert!(event(&v).unwrap_err().contains("TRANSFER_FEE"));
    }
    #[test]
    fn clmm_transfer_fee_rejected() {
        let mut v = cl();
        mutate(&mut v, 144, &1_u64.to_le_bytes());
        assert!(event(&v).unwrap_err().contains("TRANSFER_FEE"));
    }
    #[test]
    fn cpmm_overflow_rejected() {
        let mut v = cp();
        mutate(&mut v, 40, &u64::MAX.to_le_bytes());
        assert!(event(&v).is_err());
    }
    #[test]
    fn cpmm_underflow_rejected() {
        let mut v = cp();
        mutate(&mut v, 64, &u64::MAX.to_le_bytes());
        assert!(event(&v).is_err());
    }
    #[test]
    fn clmm_invalid_tick_rejected() {
        let mut v = cl();
        mutate(&mut v, 201, &500_i32.to_le_bytes());
        assert!(event(&v).unwrap_err().contains("TICK"));
    }
    #[test]
    fn clmm_zero_sqrt_rejected() {
        let mut v = cl();
        mutate(&mut v, 169, &0_u128.to_le_bytes());
        assert!(event(&v).is_err());
    }
    #[test]
    fn unknown_event_version_rejected() {
        let mut v = cp();
        let mut raw = STANDARD.decode(v["event_b64"].as_str().unwrap()).unwrap();
        raw.push(0);
        v["event_b64"] = json!(STANDARD.encode(raw));
        assert!(event(&v).unwrap_err().contains("LAYOUT"));
    }
    #[test]
    fn pool_mismatch_rejected() {
        let mut v = cp();
        mutate(&mut v, 8, &[99; 32]);
        assert!(event(&v).unwrap_err().contains("IDENTITY"));
    }
    #[test]
    fn clmm_requires_transaction() {
        let mut v = cl();
        v.as_object_mut().unwrap().remove("tx");
        assert!(event(&v).unwrap_err().contains("TX_REQUIRED"));
    }
    #[test]
    fn transaction_slot_mismatch_rejected() {
        let mut v = cl();
        v["tx"]["slot"] = json!(1);
        assert!(event(&v).unwrap_err().contains("IDENTITY"));
    }
    #[test]
    fn failed_transaction_rejected() {
        let mut v = cl();
        v["tx"]["meta"]["err"] = json!("failed");
        assert!(event(&v).is_err());
    }
    #[test]
    fn transaction_other_event_rejected() {
        let mut v = cl();
        v["tx"]["meta"]["logMessages"][1] = json!("Program log: unrelated");
        assert!(event(&v).unwrap_err().contains("EVENT"));
    }
    #[test]
    fn vault_delta_extra_operation_rejected() {
        let mut v = cl();
        v["tx"]["meta"]["postTokenBalances"][0]["uiTokenAmount"]["amount"] = json!("1");
        assert!(event(&v).unwrap_err().contains("DELTA"));
    }
    #[test]
    fn clmm_actor_direct_from_same_tx() {
        let e = event(&cl()).unwrap();
        assert!(matches!(
            e.actor_hint.unwrap().quality,
            ActorQuality::Direct(_)
        ));
    }
    #[test]
    fn actor_pool_index_cpmm_and_clmm() {
        for owner in [CPMM, CLMM] {
            let mut v = cl();
            let cfg: &Value = &v["cfg"];
            let pool = cfg["pool"].as_str().unwrap().to_owned();
            v["tx"]["transaction"]["message"]["accountKeys"][5] = json!(owner);
            if owner == CPMM {
                v["tx"]["transaction"]["message"]["instructions"][0]["accounts"] =
                    json!([0, 1, 1, 2, 3, 4]);
                v["tx"]["transaction"]["message"]["instructions"][0]["data"] =
                    json!(bs58::encode([143, 190, 90, 218, 196, 30, 51, 222]).into_string());
            }
            assert!(matches!(
                parse_actor(&v["tx"], &pool).quality,
                ActorQuality::Direct(_)
            ));
        }
    }
    #[test]
    fn failed_cpi_discards_emissions() {
        let v = cl();
        let owner = CLMM;
        let logs = json!([
            "Program ROUTER invoke [1]",
            format!("Program {owner} invoke [2]"),
            format!("Program data: {}", v["event_b64"].as_str().unwrap()),
            format!("Program {owner} failed: custom"),
            "Program ROUTER success"
        ]);
        assert!(
            payloads(
                logs.as_array().unwrap(),
                owner,
                v["cfg"]["pool"].as_str().unwrap()
            )
            .unwrap()
            .is_empty()
        );
    }
    #[test]
    fn wrong_program_cannot_spoof_event() {
        let v = cl();
        let logs = json!([
            "Program FAKE invoke [1]",
            format!("Program data: {}", v["event_b64"].as_str().unwrap()),
            "Program FAKE success"
        ]);
        assert!(
            payloads(
                logs.as_array().unwrap(),
                CLMM,
                v["cfg"]["pool"].as_str().unwrap()
            )
            .unwrap()
            .is_empty()
        );
    }
    #[test]
    fn truncated_logs_fail_closed() {
        let v = cl();
        let logs = json!([
            format!("Program {CLMM} invoke [1]"),
            format!("Program data: {}", v["event_b64"].as_str().unwrap())
        ]);
        assert!(
            payloads(
                logs.as_array().unwrap(),
                CLMM,
                v["cfg"]["pool"].as_str().unwrap()
            )
            .is_err()
        );
    }
    #[test]
    fn loaded_addresses_support_vaults() {
        let mut v = cl();
        let keys = v["tx"]["transaction"]["message"]["accountKeys"]
            .as_array()
            .unwrap()
            .clone();
        v["tx"]["transaction"]["message"]["accountKeys"] = json!(&keys[..3]);
        v["tx"]["meta"]["loadedAddresses"]["writable"] = json!(&keys[3..]);
        assert!(event(&v).is_ok());
    }
}

/// Shared per-config watchdog. Rates are preflight parameters, not a fabricated
/// historical account read. Revalidate every 10s; observations stop if freshness
/// exceeds 15s or a read fails. Any detected change invalidates/stops the session.
pub async fn config_guard(
    client: Client,
    rpc: String,
    cfg: PoolCfg,
    fresh: Arc<AtomicU64>,
    state: Arc<Mutex<EngineState>>,
    log: LogSink,
) {
    loop {
        let request=client.post(&rpc).timeout(Duration::from_secs(5)).json(&json!({"jsonrpc":"2.0","id":1,
            "method":"getAccountInfo","params":[cfg.amm_config,{"encoding":"base64","commitment":"confirmed"}]})).send().await;
        let result: Result<Value, String> = match request {
            Ok(r) => match r.error_for_status() {
                Ok(r) => r.json::<Value>().await.map_err(|e| e.to_string()),
                Err(e) => Err(e.to_string()),
            },
            Err(e) => Err(e.to_string()),
        };
        match result {
            Ok(v)
                if v.get("error").is_none()
                    && !v.pointer("/result/value").is_none_or(Value::is_null) =>
            {
                let good = v.pointer("/result/value/owner").and_then(Value::as_str) == Some(CPMM)
                    && v.pointer("/result/value/data/0").and_then(Value::as_str)
                        == Some(cfg.cpmm_config_data_b64.as_str());
                if !good {
                    fresh.store(0, Ordering::Relaxed);
                    let _ = log.send(LogRecord::Error(
                        json!({"kind":"cpmm_fee_config_changed_session_invalidated",
                        "config":cfg.amm_config,"unix_ms":now_ms()})
                        .to_string(),
                    ));
                    // The existing fatal-integrity flag also prevents a success verdict.
                    log.failed.store(true, Ordering::Relaxed);
                    return;
                }
                fresh.store(now_ms(), Ordering::Relaxed);
            }
            other => {
                fresh.store(0, Ordering::Relaxed);
                state.lock().unwrap().rpc_errors += 1;
                let _ = log.send(LogRecord::Error(
                    json!({"kind":"cpmm_fee_config_unverified_observations_blocked",
                    "config":cfg.amm_config,"error":format!("{other:?}"),"unix_ms":now_ms()})
                    .to_string(),
                ));
            }
        }
        tokio::time::sleep(Duration::from_secs(10)).await;
    }
}
