#![forbid(unsafe_code)]
#![recursion_limit = "512"]

mod m2_core;
mod raydium_protocols;
mod token_programs;
use m2_core::{post_reference, HORIZONS};

use base64::{Engine as _, engine::general_purpose::STANDARD};

use futures_util::{SinkExt, StreamExt};

use reqwest::Client;

use serde::{Deserialize, Serialize};

use serde_json::{Value, json};

use std::{
    collections::{HashMap, HashSet, VecDeque},
    env,
    error::Error,
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use tokio::sync::mpsc;

use tokio_tungstenite::{connect_async, tungstenite::Message};

const RAYDIUM_AMM_V4: &str = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8";

const WSOL: &str = "So11111111111111111111111111111111111111112";

const ACTOR_QUEUE_CAPACITY: usize = 2048;

const ACTOR_PRIORITY_BUFFER: usize = 128;

const CHECKPOINT_INTERVAL_SECS: u64 = 30;
const HORIZON_TICK_MS: u64 = 250;
const LOG_QUEUE_CAPACITY: usize = 8192;

const DASHBOARD_WRITE_INTERVAL_MS: u64 = 1000;

const METADATA_RETRY_MAX_SECS: u64 = 30;

const HISTORY_MS: u64 = 5 * 60 * 1000;

const POOL_HISTORY_MS: u64 = 10 * 60 * 1000;

const EXIT_HISTORY_MS: u64 = 8 * 60 * 60 * 1000;

const ASOF_MAX_STALENESS_MS: u64 = 10_000;

const WS_IDLE_PING_AFTER_MS: u64 = 20_000;

const WS_KEEPALIVE_MAX_MISSES: u32 = 3;

const LEADER_HALF_LIFE_MS: u64 = 6 * 60 * 60 * 1000;

const PRICE_RESERVE_MAX_DEVIATION: f64 = 0.05;

const RESERVE_RATIO_MAX_JUMP: f64 = 0.05;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
enum Side {
    Buy,
    Sell,
}

impl Side {
    fn label(self) -> &'static str {
        match self {
            Side::Buy => "BUY",
            Side::Sell => "SELL",
        }
    }
}

#[derive(Clone, Default, Serialize, Deserialize)]
struct PoolCfg {
    symbol: String,
    pool: String,
    meme_mint: String,
    #[serde(default)] owner: String,
    #[serde(default)] coin_vault: String,
    #[serde(default)] pc_vault: String,
    #[serde(default)] amm_config: String,
    #[serde(default)] cpmm_config_data_b64: String,
    #[serde(default)] protocol_fee_rate: u64,
    #[serde(default)] fund_fee_rate: u64,
    #[serde(default)] token_contract_revision: String,
    #[serde(default)] coin_token_program: String,
    #[serde(default)] pc_token_program: String,
    #[serde(default)] coin_mint_data_b64: String,
    #[serde(default)] pc_mint_data_b64: String,
    #[serde(default)] coin_vault_data_b64: String,
    #[serde(default)] pc_vault_data_b64: String,
}

const POOL_SYMBOLS: [&str; 12] = ["FARTCOIN", "BOME", "WIF", "POPCAT", "RAY", "JUP", "BONK", "PENGU", "PUMP", "USELESS", "GRASS", "PYTH"];

#[derive(Clone)]
struct PoolMeta {
    coin_mint: String,
    pc_mint: String,

    coin_decimals: u32,
    pc_decimals: u32,

    coin_is_meme: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct SwapEvent {
    symbol: String,
    pool: String,

    signature: String,
    slot: u64,

    received_unix_ms: u64,

    #[serde(default)]
    received_unix_ns: u64,

    log_type: u8,
    direction: u64,

    side: Side,

    price_sol_per_meme: f64,

    meme_qty: f64,
    quote_sol: f64,

    pool_meme_reserve: f64,
    pool_quote_reserve_sol: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    protocol: Option<Value>,
    #[serde(skip)]
    actor_hint: Option<ActorResolution>,
}

impl SwapEvent {
    fn reference_basis(&self) -> &str {
        self.protocol.as_ref().and_then(|p|p["reference_basis"].as_str())
            .unwrap_or("POST_RESERVES_DIAGNOSTIC_NOT_EXECUTION")
    }
    fn reference_post(&self) -> Option<f64> {
        if let Some(protocol) = &self.protocol {
            return protocol.get("reference_post").and_then(Value::as_f64)
                .filter(|v|v.is_finite() && *v>0.0);
        }
        post_reference(self.pool_meme_reserve, self.pool_quote_reserve_sol,
            self.meme_qty, self.quote_sol, self.side == Side::Buy)
    }
    fn log_json(&self, decode_us: f64) -> String {
        let mut value = serde_json::to_value(self).expect("finite validated swap");
        value["schema"] = json!("belial_m2_swap_v1");
        value["collector_variant"] = json!("MP12_ULTRA_V1");
        value["reference_post_price"] = json!(self.reference_post());
        value["reference_basis"] = self.protocol.as_ref().map(|p|p["reference_basis"].clone())
            .unwrap_or_else(||json!("POST_RESERVES_DIAGNOSTIC_NOT_EXECUTION"));
        value["collector_protocol_revision"] = json!("FIX2_AMM_V4_CPMM_CLMM");
        value["raw_fields_preserved"] = json!(true);
        value["collector_decode_us"] = json!(decode_us);
        value["local_timing_resolution"] = json!("UNIX_NS_LOCAL_RECEIVE_NOT_BLOCKCHAIN_TIMESTAMP");
        value.to_string()
    }
}

#[derive(Clone, Serialize, Deserialize)]
struct PoolPoint {
    ts_ms: u64,
    price: f64,
    side: Side,
    meme_qty: f64,
}

#[derive(Clone, Serialize, Deserialize)]
struct TradeObs {
    ts_ms: u64,
    side: Side,
    qty: f64,
}

#[derive(Clone, Serialize, Deserialize)]
struct WalletState {
    wallet: String,
    symbol: String,

    first_seen_ms: u64,
    last_seen_ms: u64,

    buys: u64,
    sells: u64,

    observed_inventory: f64,

    last_action: Side,

    last_buy_ms: Option<u64>,

    buy_to_sell_flips: u64,

    trades: VecDeque<TradeObs>,

    holding_samples_ms: VecDeque<u64>,
}

#[derive(Clone)]
struct PoolRuntime {
    cfg: PoolCfg,
    meta: PoolMeta,

    events: VecDeque<PoolPoint>,

    actor_resolved: u64,
    direct_actor: u64,

    swaps_total: u64,

    last_meme_reserve: f64,
    last_quote_reserve_sol: f64,

    multi_raylog_quarantined: u64,
    quality_quarantined: u64,

    last_signature: String,
    last_event_unix_ms: u64,
    last_slot: u64,
}

#[derive(Clone, Serialize, Deserialize)]
struct ExitRecord {
    event_id: String,

    symbol: String,
    wallet: String,

    event_unix_ms: u64,

    #[serde(default)]
    signal_available_unix_ms: u64,

    #[serde(default)]
    actor_resolution_latency_ms: u64,

    entry_price: f64,

    inventory_before: f64,
    sell_qty: f64,
    inventory_exit_pct: f64,

    cluster_size: u64,

    sell_quote_sol: f64,

    sell_pool_reserve_pct: f64,

    observed_inventory_pool_pct: f64,

    wallet_sell_flow_share_1s_pct: f64,
    wallet_sell_flow_share_5s_pct: f64,
    wallet_sell_flow_share_30s_pct: f64,

    #[serde(default)]
    cluster_unique_wallets_10s: u64,

    #[serde(default)]
    cluster_sell_quote_sol_10s: f64,

    #[serde(default)]
    pre_return_30s_bps: f64,

    #[serde(default)]
    pre_sell_pressure_30s_pct: f64,

    #[serde(default)]
    absorption_score: f64,

    #[serde(default)]
    sell_efficiency_5s_score: f64,

    #[serde(default)]
    regime: String,

    #[serde(default)]
    leader_quality_120s_at_entry: f64,

    #[serde(default)]
    leader_samples_120s_at_entry: u64,

    #[serde(default)]
    tail_candidate_score: f64,

    impact_score: f64,

    return_500ms_bps: Option<f64>,

    return_1s_bps: Option<f64>,

    return_2s_bps: Option<f64>,

    return_3s_bps: Option<f64>,

    return_5s_bps: Option<f64>,

    return_10s_bps: Option<f64>,

    return_30s_bps: Option<f64>,

    return_60s_bps: Option<f64>,

    return_120s_bps: Option<f64>,

    #[serde(default)]
    return_300s_bps: Option<f64>,

    #[serde(default)]
    evaluated_horizons: Vec<u64>,
    #[serde(default)]
    entry_price_basis: String,
    #[serde(default)]
    observed_entry_price: Option<f64>,
}

#[derive(Clone, Serialize)]
struct LearningRecord {
    event_id: String,
    symbol: String,
    wallet: String,

    horizon_ms: u64,

    signal_available_unix_ms: u64,
    actor_resolution_latency_ms: u64,

    return_bps: Option<f64>,
    asof_age_ms: Option<u64>,
    status: String,
    horizon_due_unix_ms: u64,
    signal_age_ms: u64,
    publication_delay_ms: u64,
    price_basis: String,
    matured_unix_ms: u64,

    regime: String,
    impact_score: f64,
    absorption_score: f64,
    sell_efficiency_5s_score: f64,
    tail_candidate_score: f64,

    sell_quote_sol: f64,
    sell_pool_reserve_pct: f64,
    cluster_size: u64,
    cluster_unique_wallets_10s: u64,
    cluster_sell_quote_sol_10s: f64,

    leader_quality_120s_at_entry: f64,
    leader_samples_120s_at_entry: u64,
}

#[derive(Clone, Serialize, Deserialize)]
struct HistoryPoint {
    t_secs: f64,

    fartcoin_ret_bps: f64,
    bome_ret_bps: f64,
    wif_ret_bps: f64,

    sell_pressure: f64,
    exit_count: f64,
}

struct EngineState {
    started_ms: u64,

    engine_status: String,

    latest_slot: u64,

    swaps_total: u64,

    decode_errors: u64,

    actor_attempts: u64,
    actor_rpc_ok: u64,

    direct_actors: u64,
    routed_actors: u64,

    rpc_errors: u64,

    invalid_signature_quarantined: u64,

    learning_records: u64,

    dropped_events: u64,

    decode_samples_us: VecDeque<f64>,

    pools: HashMap<String, PoolRuntime>,

    wallets: HashMap<String, WalletState>,

    exits: VecDeque<ExitRecord>,

    history: VecDeque<HistoryPoint>,
}

#[derive(Clone, Serialize, Deserialize)]
struct PoolCheckpoint {
    symbol: String,

    events: VecDeque<PoolPoint>,

    actor_resolved: u64,
    direct_actor: u64,

    swaps_total: u64,

    last_meme_reserve: f64,
    last_quote_reserve_sol: f64,

    multi_raylog_quarantined: u64,

    #[serde(default)]
    quality_quarantined: u64,

    #[serde(default)]
    last_signature: String,

    #[serde(default)]
    last_event_unix_ms: u64,

    #[serde(default)]
    last_slot: u64,
}

#[derive(Clone, Serialize, Deserialize)]
struct EngineCheckpoint {
    schema: String,

    saved_unix_ms: u64,

    active_elapsed_ms: u64,

    latest_slot: u64,

    swaps_total: u64,

    decode_errors: u64,

    actor_attempts: u64,
    actor_rpc_ok: u64,

    direct_actors: u64,
    routed_actors: u64,

    rpc_errors: u64,

    #[serde(default)]
    invalid_signature_quarantined: u64,

    #[serde(default)]
    quality_quarantined: u64,

    #[serde(default)]
    learning_records: u64,

    dropped_events: u64,

    decode_samples_us: VecDeque<f64>,

    wallets: HashMap<String, WalletState>,

    exits: VecDeque<ExitRecord>,

    history: VecDeque<HistoryPoint>,

    pools: Vec<PoolCheckpoint>,
}

#[derive(Clone, Debug)]
enum ActorQuality {
    Direct(String),
    Routed,
    Ambiguous,
}

#[derive(Clone, Debug)]
struct ActorResolution {
    quality: ActorQuality,
}

#[derive(Debug)]
enum LogRecord {
    Swap(String),
    Actor(String),
    Exit(String),
    Learning(String),
    System(String),
    Error(String),
}

#[derive(Clone)]
struct LogSink {
    tx: std::sync::mpsc::SyncSender<LogRecord>,
    failed: Arc<AtomicBool>,
}
impl LogSink {
    fn send(&self, record: LogRecord) -> Result<(), String> {
        self.tx.try_send(record).map_err(|e| {
            self.failed.store(true, Ordering::Relaxed);
            format!("M2_LOG_ENQUEUE_FAILED:{e}")
        })
    }
}
#[derive(Default, Serialize)]
struct WriterStats { rows: u64, bytes: u64, sync_calls: u64 }


#[derive(Clone, Serialize)]
struct PoolMetric {
    symbol: String,

    price: f64,
    return_1m_bps: f64,

    swaps_per_min: f64,
    buy_sell_imbalance: f64,

    unique_wallets_5m: u64,
    direct_actor_pct: f64,

    quality_quarantined: u64,

    median_interarrival_ms: f64,
}

#[derive(Clone, Serialize)]
struct WalletMetric {
    wallet: String,
    symbol: String,

    automation_score: f64,
    fast_money_score: f64,

    leader_quality_score: f64,
    leader_samples: u64,
    leader_hit_rate_pct: f64,
    leader_median_short_bps: f64,

    leader_quality_30s_score: f64,
    leader_quality_60s_score: f64,
    leader_quality_300s_score: f64,

    buys: u64,
    sells: u64,

    observed_inventory: f64,

    observed_inventory_pool_pct: f64,

    sell_flow_share_1s_pct: f64,
    sell_flow_share_5s_pct: f64,
    sell_flow_share_30s_pct: f64,

    capital_concentration_score: f64,

    turnover_5m: f64,

    median_interarrival_ms: f64,

    last_action: String,
    actor_quality: String,
}

#[derive(Clone, Serialize)]
struct DashboardExit {
    symbol: String,
    wallet: String,

    age_ms: u64,

    signal_available_unix_ms: u64,
    actor_resolution_latency_ms: u64,

    inventory_exit_pct: f64,
    cluster_size: u64,

    automation_score: f64,
    fast_money_score: f64,

    impact_score: f64,

    sell_quote_sol: f64,
    sell_pool_reserve_pct: f64,

    wallet_sell_flow_share_1s_pct: f64,
    wallet_sell_flow_share_5s_pct: f64,
    wallet_sell_flow_share_30s_pct: f64,

    cluster_unique_wallets_10s: u64,
    cluster_sell_quote_sol_10s: f64,

    pre_return_30s_bps: f64,
    pre_sell_pressure_30s_pct: f64,

    absorption_score: f64,
    sell_efficiency_5s_score: f64,

    regime: String,

    leader_quality_120s_at_entry: f64,
    leader_samples_120s_at_entry: u64,

    tail_candidate_score: f64,

    return_500ms_bps: Option<f64>,

    return_1s_bps: Option<f64>,

    return_2s_bps: Option<f64>,

    return_5s_bps: Option<f64>,

    return_10s_bps: Option<f64>,

    return_30s_bps: Option<f64>,

    return_60s_bps: Option<f64>,

    return_120s_bps: Option<f64>,

    return_300s_bps: Option<f64>,

    status: String,
}

#[derive(Serialize)]
struct DashboardState {
    schema: &'static str,

    mode: &'static str,

    engine_status: String,

    capture_elapsed_secs: u64,

    updated_unix_ms: u64,

    slot: u64,

    swaps_total: u64,

    swaps_per_min: f64,

    direct_actor_pct: f64,

    unique_wallets: u64,

    bot_like_candidates: u64,

    active_exit_candidates: u64,

    actor_queue_depth: u64,

    rpc_errors: u64,

    invalid_signature_quarantined: u64,
    quality_quarantined: u64,
    learning_records: u64,

    dropped_events: u64,

    decode_p95_us: f64,

    pools: Vec<PoolMetric>,

    wallets: Vec<WalletMetric>,

    exits: Vec<DashboardExit>,

    history: Vec<HistoryPoint>,
}

#[derive(Clone)]
struct WalletFeature {
    key: String,

    wallet: String,
    symbol: String,

    n5: usize,

    buys: u64,
    sells: u64,

    inventory: f64,

    inventory_pool_pct: f64,

    flow_share_1s_pct: f64,
    flow_share_5s_pct: f64,
    flow_share_30s_pct: f64,

    last_action: String,

    median_dt: f64,

    activity: f64,
    cadence: f64,
    regularity: f64,
    size_repeat: f64,
    cycle_rate: f64,

    turnover: f64,
    holding_speed: f64,
    burst: f64,

    evidence_factor: f64,
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn now_ns() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

// A1 changes only source scope/lifecycle. Token, pool and swap validators remain mandatory.
const A1_MINTS: [&str;12] = [
 "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump","ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82",
 "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm","7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
 "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R","JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
 "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263","2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv",
 "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn","Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk",
 "Grass7B4RdKfBCjTKgSqnXkqjwiGvQyFbuSCUJr3XXjs","HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"];
fn a1_scope() -> bool { matches!(std::env::var("BELIAL_A1_SENSOR").as_deref(),Ok("1")) }
fn check_source_scope(cfgs:&[PoolCfg], isolated:bool)->Result<(),String> {
 let count=if isolated {1} else {12};
 if cfgs.len()!=count {return Err(format!("SOURCE_SCOPE_REQUIRES_{count}_APPROVED_POOLS"));}
 for (i,cfg) in cfgs.iter().enumerate() {
  let index=POOL_SYMBOLS.iter().position(|s|*s==cfg.symbol.as_str()).ok_or("UNKNOWN_CANONICAL_SYMBOL")?;
  if !isolated && index!=i {return Err("SOURCE_SCOPE_POOL_ORDER".into());}
  if cfg.meme_mint!=A1_MINTS[index] {return Err("SOURCE_SCOPE_CANONICAL_MINT_MISMATCH".into());}
 }
 Ok(())
}

fn pools() -> Vec<PoolCfg> {
    if let Ok(path) = std::env::var("BELIAL_T3_POOL_MANIFEST") {
        let raw = std::fs::read_to_string(path).expect("T3 validated pool manifest missing");
        let value: Value = serde_json::from_str(&raw).expect("T3 invalid manifest JSON");
        assert_eq!(value["ok"], json!(true), "T3 pool preflight not approved");
        let cfgs: Vec<PoolCfg> = serde_json::from_value(value["pools"].clone()).expect("T3 pool schema");
        check_source_scope(&cfgs,a1_scope()).expect("A1 source scope not approved");
        for (i,cfg) in cfgs.iter().enumerate() {
            assert_eq!(cfg.token_contract_revision, token_programs::REVISION, "FIX4 token preflight required");
            assert!(token_programs::supported(&cfg.coin_token_program) && token_programs::supported(&cfg.pc_token_program), "FIX4 token programs required");
            if !a1_scope() { assert_eq!(cfg.symbol, POOL_SYMBOLS[i], "T3 pool order/identity"); }
            assert!([RAYDIUM_AMM_V4, raydium_protocols::CPMM, raydium_protocols::CLMM].contains(&cfg.owner.as_str()), "T3 unsupported pool owner");
            for value in [&cfg.pool, &cfg.meme_mint] {
                assert_eq!(bs58::decode(value).into_vec().expect("T3 invalid pubkey").len(),32);
            }
        }
        return cfgs;
    }
    #[cfg(test)] {
        let mut cfgs = legacy_test_pools();
        for (i,symbol) in POOL_SYMBOLS.iter().enumerate().skip(6) {
            cfgs.push(PoolCfg { symbol: symbol.to_string(),
                pool: bs58::encode([i as u8;32]).into_string(),
                meme_mint: bs58::encode([(i+32) as u8;32]).into_string(), ..Default::default() });
        }
        cfgs
    }
    #[cfg(not(test))] { panic!("BELIAL_T3_POOL_MANIFEST required: refuse unvalidated identity"); }
}
#[cfg(test)]
fn legacy_test_pools() -> Vec<PoolCfg> {
    vec![
        PoolCfg {
            symbol: "FARTCOIN".to_string(),

            pool: "Bzc9NZfMqkXR6fz1DBph7BDf9BroyEf6pnzESP7v5iiw".to_string(),

            meme_mint: "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump".to_string(),
            ..Default::default()
        },
        PoolCfg {
            symbol: "BOME".to_string(),

            pool: "DSUvc5qf5LJHHV5e2tD184ixotSnCnwj7i4jJa4Xsrmt".to_string(),

            meme_mint: "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82".to_string(),
            ..Default::default()
        },
        PoolCfg {
            symbol: "WIF".to_string(),

            pool: "EP2ib6dYdEeqD8MfE2ezHCxX3kP3K2eLKkirfPm5eyMx".to_string(),

            meme_mint: "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm".to_string(),
            ..Default::default()
        },
        PoolCfg {
            symbol: "POPCAT".to_string(),
            pool: "FRhB8L7Y9Qq41qZXYLtC2nw8An1RJfLLxRF2x9RwLLMo".to_string(),
            meme_mint: "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr".to_string(),
            ..Default::default()
        },
        PoolCfg {
            symbol: "RAY".to_string(),
            pool: "AVs9TA4nWDzfPJE9gGVNJMVhcQy3V9PGazuz33BfG2RA".to_string(),
            meme_mint: "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R".to_string(),
            ..Default::default()
        },
        PoolCfg {
            symbol: "JUP".to_string(),
            pool: "EYErUp5muPYEEkeaUCY22JibeZX7E9UuMcJFZkmNAN7c".to_string(),
            meme_mint: "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN".to_string(),
            ..Default::default()
        },
    ]
}

fn u64_le(data: &[u8], offset: usize) -> Result<u64, String> {
    let end = offset + 8;

    let slice = data
        .get(offset..end)
        .ok_or_else(|| "short u64".to_string())?;

    let mut bytes = [0u8; 8];

    bytes.copy_from_slice(slice);

    Ok(u64::from_le_bytes(bytes))
}

async fn fetch_pool_meta(
    client: &Client,
    rpc: &str,
    cfg: &PoolCfg,
) -> Result<PoolMeta, Box<dyn Error>> {
    let body = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [
            cfg.pool,
            {
                "encoding": "base64",
                "commitment": "confirmed"
            }
        ]
    });

    let response = client.post(rpc).json(&body).send().await?;

    let value: Value = response.error_for_status()?.json().await?;

    if let Some(error) = value.get("error") {
        return Err(format!("getAccountInfo {}: {}", cfg.symbol, error).into());
    }

    let encoded = value
        .pointer("/result/value/data/0")
        .and_then(Value::as_str)
        .ok_or("pool account data ausente")?;

    let data = STANDARD.decode(encoded)?;
    let owner=value.pointer("/result/value/owner").and_then(Value::as_str).ok_or("pool owner missing")?;
    if owner!=cfg.owner {return Err("pool owner changed after preflight".into());}
    if owner==raydium_protocols::CPMM || owner==raydium_protocols::CLMM {
        if owner==raydium_protocols::CPMM {
            let v:Value=client.post(rpc).json(&json!({"jsonrpc":"2.0","id":1,"method":"getAccountInfo",
                "params":[cfg.amm_config,{"encoding":"base64","commitment":"confirmed"}]})).send().await?.error_for_status()?.json().await?;
            if v.pointer("/result/value/owner").and_then(Value::as_str)!=Some(owner)
                || v.pointer("/result/value/data/0").and_then(Value::as_str)!=Some(cfg.cpmm_config_data_b64.as_str()) {
                return Err("CPMM_CONFIG_CHANGED_AFTER_PREFLIGHT".into());
            }
        }
        return raydium_protocols::meta(cfg,owner,&data).map_err(Into::into);
    }

    if value.pointer("/result/value/owner").and_then(Value::as_str) != Some("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8") {
        return Err(format!("{} unsupported pool owner", cfg.symbol).into());
    }
    if data.len() != 752 {
        return Err(format!("{} pool len {} < 464", cfg.symbol, data.len()).into());
    }

    let coin_decimals = u64_le(&data, 32)? as u32;

    let pc_decimals = u64_le(&data, 40)? as u32;

    let coin_mint = bs58::encode(&data[400..432]).into_string();

    let pc_mint = bs58::encode(&data[432..464]).into_string();

    let pair_ok = (coin_mint == cfg.meme_mint && pc_mint == WSOL)
        || (pc_mint == cfg.meme_mint && coin_mint == WSOL);

    if !pair_ok {
        return Err(format!(
            "{} unexpected pair coin={} pc={}",
            cfg.symbol, coin_mint, pc_mint
        )
        .into());
    }

    let meta = PoolMeta {
        coin_is_meme: coin_mint == cfg.meme_mint,

        coin_mint,
        pc_mint,

        coin_decimals,
        pc_decimals,
    };
    token_programs::verify_cfg(cfg, &meta)?;
    Ok(meta)
}

async fn fetch_pool_meta_with_retry(client: &Client, rpc: &str, cfg: &PoolCfg) -> Result<PoolMeta, Box<dyn Error>> {
    let mut attempt = 0u64;

    loop {
        match fetch_pool_meta(client, rpc, cfg).await {
            Ok(meta) => {
                if attempt > 0 {
                    eprintln!(
                        "R5D_METADATA_RECOVERED pool={} attempts={}",
                        cfg.symbol,
                        attempt + 1
                    );
                }

                return Ok(meta);
            }

            Err(error) => {
                attempt += 1;
                if attempt>=5 {return Err(format!("METADATA_RETRIES_EXHAUSTED:{}:{error}",cfg.symbol).into());}

                let exponent = attempt.min(5) as u32;

                let wait = (1u64 << exponent).min(METADATA_RETRY_MAX_SECS);

                eprintln!(
                    "R5D_METADATA_RETRY pool={} attempt={} wait_s={} error={}",
                    cfg.symbol, attempt, wait, error
                );

                tokio::time::sleep(Duration::from_secs(wait)).await;
            }
        }
    }
}

fn amount_ui(raw: u64, decimals: u32) -> f64 {
    raw as f64 / 10f64.powi(decimals as i32)
}

fn decode_raylog(
    encoded: &str,
    cfg: &PoolCfg,
    meta: &PoolMeta,

    signature: &str,
    slot: u64,

    received_unix_ms: u64,
    received_unix_ns: u64,
) -> Result<SwapEvent, String> {
    let data = STANDARD
        .decode(encoded)
        .map_err(|e| format!("base64: {e}"))?;

    if data.len() < 57 {
        return Err(format!("ray_log len={}", data.len()));
    }

    let log_type = data[0];

    if log_type != 3 && log_type != 4 {
        return Err(format!("unsupported log_type={log_type}"));
    }

    let f1 = u64_le(&data, 1)?;

    let f2 = u64_le(&data, 9)?;

    let direction = u64_le(&data, 17)?;

    let f7 = u64_le(&data, 49)?;

    let pool_coin_raw = u64_le(&data, 33)?;

    let pool_pc_raw = u64_le(&data, 41)?;

    if direction != 1 && direction != 2 {
        return Err(format!("direction={direction}"));
    }

    let (actual_in, actual_out) = if log_type == 3 { (f1, f7) } else { (f7, f2) };

    if actual_in == 0 || actual_out == 0 {
        return Err("zero amount".to_string());
    }

    let (input_is_coin, input_decimals, output_decimals) = if direction == 2 {
        (true, meta.coin_decimals, meta.pc_decimals)
    } else {
        (false, meta.pc_decimals, meta.coin_decimals)
    };

    let input_ui = amount_ui(actual_in, input_decimals);

    let output_ui = amount_ui(actual_out, output_decimals);

    let pool_coin_ui = amount_ui(pool_coin_raw, meta.coin_decimals);

    let pool_pc_ui = amount_ui(pool_pc_raw, meta.pc_decimals);

    let (pool_meme_reserve, pool_quote_reserve_sol) = if meta.coin_is_meme {
        (pool_coin_ui, pool_pc_ui)
    } else {
        (pool_pc_ui, pool_coin_ui)
    };

    if input_ui <= 0.0 || output_ui <= 0.0 {
        return Err("non-positive ui amount".to_string());
    }

    let input_is_meme = if input_is_coin {
        meta.coin_is_meme
    } else {
        !meta.coin_is_meme
    };

    let (side, meme_qty, quote_sol, price) = if input_is_meme {
        let meme_qty = input_ui;

        let quote_sol = output_ui;

        (Side::Sell, meme_qty, quote_sol, quote_sol / meme_qty)
    } else {
        let quote_sol = input_ui;

        let meme_qty = output_ui;

        (Side::Buy, meme_qty, quote_sol, quote_sol / meme_qty)
    };

    if !price.is_finite() || price <= 0.0 {
        return Err("invalid price".to_string());
    }

    Ok(SwapEvent {
        symbol: cfg.symbol.clone(),

        pool: cfg.pool.clone(),

        signature: signature.to_string(),

        slot,

        received_unix_ms,
        received_unix_ns,

        log_type,
        direction,

        side,

        price_sol_per_meme: price,

        meme_qty,
        quote_sol,

        pool_meme_reserve,
        pool_quote_reserve_sol,
        protocol: None,
        actor_hint: None,
    })
}

fn placeholder_signature(signature: &str) -> bool {
    signature.len() >= 32 && signature.chars().all(|c| c == '1')
}

fn validate_event_quality(pool: &PoolRuntime, event: &SwapEvent) -> Result<(), String> {
    if !event.pool_meme_reserve.is_finite()
        || !event.pool_quote_reserve_sol.is_finite()
        || event.pool_meme_reserve <= 0.0
        || event.pool_quote_reserve_sol <= 0.0
    {
        return Err("invalid_pool_reserves".to_string());
    }

    // CLMM vault balances are real custody balances, NOT active depth or spot price.
    // Exact deltas, sqrt/tick consistency and committed event identity were checked
    // against the same transaction; a vault-ratio price filter is invalid here.
    if event.protocol.as_ref().is_some_and(|p|p["pool_kind"]=="CLMM") {
        if event.reference_post().is_none() || event.meme_qty<=0.0 || event.quote_sol<=0.0 {
            return Err("clmm_reference_invalid".into());
        }
        return Ok(());
    }
    let reserve_ratio = event.pool_quote_reserve_sol / event.pool_meme_reserve;

    if !reserve_ratio.is_finite() || reserve_ratio <= 0.0 {
        return Err("invalid_reserve_ratio".to_string());
    }

    let execution_deviation = (event.price_sol_per_meme / reserve_ratio - 1.0).abs();

    // R5C2: preserve legitimate large-impact swaps instead of
    // treating a fixed 5% execution/reserve difference as corruption.
    // The floor catches the 6h dust/wrong-reserve anomalies, while the
    // dynamic allowance grows with the trade fraction of the pool.
    let meme_fraction = (event.meme_qty / event.pool_meme_reserve).abs();

    let quote_fraction = (event.quote_sol / event.pool_quote_reserve_sol).abs();

    let trade_fraction = meme_fraction.max(quote_fraction);

    let execution_allowed = PRICE_RESERVE_MAX_DEVIATION
        .max(2.5 * trade_fraction)
        .min(0.25);

    if execution_deviation > execution_allowed {
        return Err(format!(
            "execution_reserve_deviation={:.6};allowed={:.6};trade_fraction={:.6}",
            execution_deviation, execution_allowed, trade_fraction
        ));
    }

    if pool.last_meme_reserve > 0.0 && pool.last_quote_reserve_sol > 0.0 {
        let previous_ratio = pool.last_quote_reserve_sol / pool.last_meme_reserve;

        if previous_ratio.is_finite() && previous_ratio > 0.0 {
            let continuity_jump = (reserve_ratio / previous_ratio - 1.0).abs();

            let continuity_allowed = RESERVE_RATIO_MAX_JUMP.max(2.0 * trade_fraction).min(0.25);

            if continuity_jump > continuity_allowed {
                return Err(format!(
                    "reserve_ratio_jump={:.6};allowed={:.6};trade_fraction={:.6}",
                    continuity_jump, continuity_allowed, trade_fraction
                ));
            }
        }
    }

    Ok(())
}

fn median(values: &mut [f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }

    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let n = values.len();

    if n % 2 == 1 {
        values[n / 2]
    } else {
        (values[n / 2 - 1] + values[n / 2]) / 2.0
    }
}

fn cv(values: &[f64]) -> f64 {
    if values.len() < 2 {
        return 1.0;
    }

    let mean = values.iter().sum::<f64>() / values.len() as f64;

    if mean.abs() < 1e-12 {
        return 1.0;
    }

    let variance = values
        .iter()
        .map(|v| {
            let d = *v - mean;

            d * d
        })
        .sum::<f64>()
        / values.len() as f64;

    variance.sqrt() / mean.abs()
}

fn percentile_rank(values: &[f64], target: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }

    if values.len() == 1 {
        return 100.0;
    }

    let below = values.iter().filter(|v| **v <= target).count();

    100.0 * below as f64 / values.len() as f64
}

fn pool_sell_qty(pool: &PoolRuntime, end_ms: u64, window_ms: u64) -> f64 {
    let start = end_ms.saturating_sub(window_ms);

    pool.events
        .iter()
        .filter(|e| e.ts_ms >= start && e.ts_ms <= end_ms && e.side == Side::Sell)
        .map(|e| e.meme_qty)
        .sum()
}

fn wallet_sell_qty(wallet: &WalletState, end_ms: u64, window_ms: u64) -> f64 {
    let start = end_ms.saturating_sub(window_ms);

    wallet
        .trades
        .iter()
        .filter(|e| e.ts_ms >= start && e.ts_ms <= end_ms && e.side == Side::Sell)
        .map(|e| e.qty)
        .sum()
}

fn flow_share_pct(wallet_qty: f64, total_qty: f64) -> f64 {
    if total_qty <= 0.0 {
        return 0.0;
    }

    (wallet_qty / total_qty * 100.0).clamp(0.0, 100.0)
}

fn wallet_feature(key: &str, wallet: &WalletState, state: &EngineState, now: u64) -> WalletFeature {
    let cutoff = now.saturating_sub(HISTORY_MS);

    let recent = wallet
        .trades
        .iter()
        .filter(|t| t.ts_ms >= cutoff)
        .cloned()
        .collect::<Vec<_>>();

    let n = recent.len();

    let mut intervals = Vec::<f64>::new();

    for pair in recent.windows(2) {
        intervals.push(pair[1].ts_ms.saturating_sub(pair[0].ts_ms) as f64);
    }

    let mut median_values = intervals.clone();

    let median_dt = median(&mut median_values);

    let cadence = if median_dt > 0.0 {
        1.0 / median_dt
    } else {
        0.0
    };

    let regularity = if intervals.len() >= 2 {
        1.0 / (1.0 + cv(&intervals))
    } else {
        0.0
    };

    let sizes = recent
        .iter()
        .map(|t| t.qty.abs())
        .filter(|v| *v > 0.0)
        .collect::<Vec<_>>();

    let size_repeat = if sizes.len() >= 3 {
        1.0 / (1.0 + cv(&sizes))
    } else {
        0.0
    };

    let volume = sizes.iter().sum::<f64>();

    let avg_size = if sizes.is_empty() {
        0.0
    } else {
        volume / sizes.len() as f64
    };

    let denominator = wallet.observed_inventory.abs().max(avg_size).max(1e-12);

    let turnover = volume / denominator;

    let cycle_rate = wallet.buy_to_sell_flips as f64
        / (wallet
            .buys
            .saturating_add(wallet.sells)
            .saturating_sub(1)
            .max(1)) as f64;

    let holding_speed = if wallet.holding_samples_ms.is_empty() {
        0.0
    } else {
        let mut h = wallet
            .holding_samples_ms
            .iter()
            .map(|v| *v as f64)
            .collect::<Vec<_>>();

        let m = median(&mut h);

        if m > 0.0 { 1.0 / m } else { 0.0 }
    };

    let mut max_in_2s = 0usize;

    for i in 0..recent.len() {
        let start = recent[i].ts_ms;

        let mut count = 1usize;

        for j in (i + 1)..recent.len() {
            if recent[j].ts_ms.saturating_sub(start) <= 2000 {
                count += 1;
            } else {
                break;
            }
        }

        max_in_2s = max_in_2s.max(count);
    }

    let evidence_factor = (n as f64 / 8.0).clamp(0.0, 1.0);

    let (inventory_pool_pct, flow_share_1s_pct, flow_share_5s_pct, flow_share_30s_pct) =
        if let Some(pool) = state.pools.get(&wallet.symbol) {
            let inventory_pct = if pool.last_meme_reserve > 0.0 {
                wallet.observed_inventory.max(0.0) / pool.last_meme_reserve * 100.0
            } else {
                0.0
            };

            let w1 = wallet_sell_qty(wallet, now, 1_000);

            let w5 = wallet_sell_qty(wallet, now, 5_000);

            let w30 = wallet_sell_qty(wallet, now, 30_000);

            let p1 = pool_sell_qty(pool, now, 1_000);

            let p5 = pool_sell_qty(pool, now, 5_000);

            let p30 = pool_sell_qty(pool, now, 30_000);

            (
                inventory_pct,
                flow_share_pct(w1, p1),
                flow_share_pct(w5, p5),
                flow_share_pct(w30, p30),
            )
        } else {
            (0.0, 0.0, 0.0, 0.0)
        };

    WalletFeature {
        key: key.to_string(),

        wallet: wallet.wallet.clone(),

        symbol: wallet.symbol.clone(),

        n5: n,

        buys: wallet.buys,

        sells: wallet.sells,

        inventory: wallet.observed_inventory,

        inventory_pool_pct,

        flow_share_1s_pct,
        flow_share_5s_pct,
        flow_share_30s_pct,

        last_action: wallet.last_action.label().to_string(),

        median_dt,

        activity: n as f64,

        cadence,
        regularity,
        size_repeat,
        cycle_rate,

        turnover,
        holding_speed,

        burst: max_in_2s as f64,

        evidence_factor,
    }
}

#[derive(Clone, Copy, Default)]
struct LeaderProfile {
    samples: u64,
    effective_samples: f64,
    hit_rate_pct: f64,
    median_short_bps: f64,
    quality_score: f64,
}

fn exit_return_for_horizon(exit: &ExitRecord, horizon_ms: u64) -> Option<f64> {
    match horizon_ms {
        30_000 => exit.return_30s_bps,

        60_000 => exit.return_60s_bps,

        120_000 => exit.return_120s_bps,

        300_000 => exit.return_300s_bps,

        _ => None,
    }
}

fn weighted_median(mut values: Vec<(f64, f64)>) -> f64 {
    if values.is_empty() {
        return 0.0;
    }

    values.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));

    let total = values.iter().map(|(_, weight)| *weight).sum::<f64>();

    if total <= 0.0 {
        return values[values.len() / 2].0;
    }

    let target = total / 2.0;

    let mut cumulative = 0.0;

    for (value, weight) in values {
        cumulative += weight;

        if cumulative >= target {
            return value;
        }
    }

    0.0
}

fn leader_profile(
    state: &EngineState,
    wallet: &str,
    symbol: &str,
    horizon_ms: u64,
    regime: Option<&str>,
    now: u64,
) -> LeaderProfile {
    let mut weighted_edges = Vec::<(f64, f64)>::new();

    let mut weighted_wins = 0.0;

    let mut total_weight = 0.0;

    let mut samples = 0u64;

    for exit in &state.exits {
        if exit.wallet != wallet || exit.symbol != symbol {
            continue;
        }

        if let Some(expected) = regime {
            if exit.regime != expected {
                continue;
            }
        }

        // No future leakage: a reputation sample is eligible only
        // after this horizon had time to mature before `now`.
        if exit.event_unix_ms.saturating_add(horizon_ms) > now {
            continue;
        }

        let Some(raw_return) = exit_return_for_horizon(exit, horizon_ms) else {
            continue;
        };

        let age = now.saturating_sub(exit.event_unix_ms);

        let decay = 0.5_f64.powf(age as f64 / LEADER_HALF_LIFE_MS as f64);

        let edge = (-raw_return).clamp(-500.0, 500.0);

        weighted_edges.push((edge, decay));

        if edge > 0.0 {
            weighted_wins += decay;
        }

        total_weight += decay;
        samples += 1;
    }

    if samples == 0 || total_weight <= 0.0 {
        return LeaderProfile::default();
    }

    let median_short_bps = weighted_median(weighted_edges);

    let hit_rate_pct = 100.0 * weighted_wins / total_weight;

    let edge_score = (50.0 + median_short_bps * 1.5).clamp(0.0, 100.0);

    let evidence = total_weight / (total_weight + 8.0);

    let quality_score = ((0.60 * edge_score + 0.40 * hit_rate_pct) * evidence).clamp(0.0, 100.0);

    LeaderProfile {
        samples,
        effective_samples: total_weight,
        hit_rate_pct,
        median_short_bps,
        quality_score,
    }
}

fn score_wallets(
    state: &EngineState,
    now: u64,
) -> (Vec<WalletMetric>, HashMap<String, (f64, f64)>, u64) {
    let features = state
        .wallets
        .iter()
        .map(|(key, wallet)| wallet_feature(key, wallet, state, now))
        .collect::<Vec<_>>();

    let mut scores = HashMap::<String, (f64, f64)>::new();

    let mut metrics = Vec::<WalletMetric>::new();

    let symbols = POOL_SYMBOLS.into_iter().filter(|symbol| state.pools.contains_key(*symbol));

    for symbol in symbols {
        let group = features
            .iter()
            .filter(|f| f.symbol == symbol)
            .collect::<Vec<_>>();

        if group.is_empty() {
            continue;
        }

        let activity = group.iter().map(|x| x.activity).collect::<Vec<_>>();

        let cadence = group.iter().map(|x| x.cadence).collect::<Vec<_>>();

        let regularity = group.iter().map(|x| x.regularity).collect::<Vec<_>>();

        let size_repeat = group.iter().map(|x| x.size_repeat).collect::<Vec<_>>();

        let cycle = group.iter().map(|x| x.cycle_rate).collect::<Vec<_>>();

        let turnover = group.iter().map(|x| x.turnover).collect::<Vec<_>>();

        let holding = group.iter().map(|x| x.holding_speed).collect::<Vec<_>>();

        let burst = group.iter().map(|x| x.burst).collect::<Vec<_>>();

        let capital = group
            .iter()
            .map(|x| x.inventory_pool_pct)
            .collect::<Vec<_>>();

        for f in group {
            let p_activity = percentile_rank(&activity, f.activity);

            let p_cadence = percentile_rank(&cadence, f.cadence);

            let p_regularity = percentile_rank(&regularity, f.regularity);

            let p_size = percentile_rank(&size_repeat, f.size_repeat);

            let p_cycle = percentile_rank(&cycle, f.cycle_rate);

            let raw_auto = 0.30 * p_activity
                + 0.25 * p_cadence
                + 0.20 * p_regularity
                + 0.15 * p_size
                + 0.10 * p_cycle;

            let automation = (raw_auto * f.evidence_factor).clamp(0.0, 100.0);

            let p_turnover = percentile_rank(&turnover, f.turnover);

            let p_holding = percentile_rank(&holding, f.holding_speed);

            let p_burst = percentile_rank(&burst, f.burst);

            let p_capital = percentile_rank(&capital, f.inventory_pool_pct);

            let raw_fast = 0.30 * automation
                + 0.20 * p_turnover
                + 0.15 * p_holding
                + 0.15 * p_burst
                + 0.20 * p_capital;

            let fast = (raw_fast * f.evidence_factor).clamp(0.0, 100.0);

            let leader_30 = leader_profile(state, &f.wallet, &f.symbol, 30_000, None, now);

            let leader_60 = leader_profile(state, &f.wallet, &f.symbol, 60_000, None, now);

            let leader_120 = leader_profile(state, &f.wallet, &f.symbol, 120_000, None, now);

            let leader_300 = leader_profile(state, &f.wallet, &f.symbol, 300_000, None, now);

            let leader_samples = leader_120.samples;

            let leader_hit_rate_pct = leader_120.hit_rate_pct;

            let leader_median_short_bps = leader_120.median_short_bps;

            let leader_quality_score = leader_120.quality_score;

            scores.insert(f.key.clone(), (automation, fast));

            metrics.push(WalletMetric {
                wallet: f.wallet.clone(),

                symbol: f.symbol.clone(),

                automation_score: automation,

                fast_money_score: fast,

                leader_quality_score,

                leader_samples,

                leader_hit_rate_pct,

                leader_median_short_bps,

                leader_quality_30s_score: leader_30.quality_score,

                leader_quality_60s_score: leader_60.quality_score,

                leader_quality_300s_score: leader_300.quality_score,

                buys: f.buys,

                sells: f.sells,

                observed_inventory: f.inventory,

                observed_inventory_pool_pct: f.inventory_pool_pct,

                sell_flow_share_1s_pct: f.flow_share_1s_pct,

                sell_flow_share_5s_pct: f.flow_share_5s_pct,

                sell_flow_share_30s_pct: f.flow_share_30s_pct,

                capital_concentration_score: p_capital,

                turnover_5m: f.turnover,

                median_interarrival_ms: f.median_dt,

                last_action: f.last_action.clone(),

                actor_quality: "DIRECT".to_string(),
            });
        }
    }

    metrics.sort_by(|a, b| {
        b.fast_money_score
            .partial_cmp(&a.fast_money_score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let top_like = features
        .iter()
        .filter(|f| f.n5 >= 5)
        .filter(|f| scores.get(&f.key).map(|(a, _)| *a >= 90.0).unwrap_or(false))
        .count() as u64;

    (metrics, scores, top_like)
}

fn return_bps(entry: f64, current: f64) -> Option<f64> {
    if !entry.is_finite() || !current.is_finite() || entry <= 0.0 || current <= 0.0 {
        return None;
    }

    Some((current / entry - 1.0) * 10_000.0)
}

fn pool_price_asof(
    points: &VecDeque<PoolPoint>,
    target_ms: u64,
    max_staleness_ms: u64,
) -> Option<(f64, u64)> {
    let point = points.iter().rev().find(|p| p.ts_ms <= target_ms)?;

    let age = target_ms.saturating_sub(point.ts_ms);

    if age > max_staleness_ms {
        return None;
    }

    Some((point.price, age))
}

fn pool_sell_pressure_pct(pool: &PoolRuntime, end_ms: u64, window_ms: u64) -> f64 {
    let start = end_ms.saturating_sub(window_ms);

    let mut buys = 0.0;

    let mut sells = 0.0;

    for event in &pool.events {
        if event.ts_ms < start || event.ts_ms > end_ms {
            continue;
        }

        match event.side {
            Side::Buy => buys += event.meme_qty,

            Side::Sell => sells += event.meme_qty,
        }
    }

    let total = buys + sells;

    if total <= 0.0 {
        return 0.0;
    }

    (100.0 * (sells - buys) / total).clamp(-100.0, 100.0)
}

fn exit_regime_context(state: &EngineState, event: &SwapEvent) -> (f64, f64, f64, String) {
    let Some(pool) = state.pools.get(&event.symbol) else {
        return (0.0, 0.0, 0.0, "UNKNOWN".to_string());
    };

    let pre_end = event.received_unix_ms.saturating_sub(1);

    let target = pre_end.saturating_sub(30_000);

    let pre_return_30s_bps = pool_price_asof(&pool.events, target, ASOF_MAX_STALENESS_MS)
        .and_then(|(price, _)| event.reference_post().and_then(|current| return_bps(price, current)))
        .unwrap_or(0.0);

    let sell_pressure = pool_sell_pressure_pct(pool, pre_end, 30_000);

    let sell_dominance = (sell_pressure.max(0.0) / 100.0).clamp(0.0, 1.0);

    let price_resilience = (pre_return_30s_bps.max(0.0) / 50.0).clamp(0.0, 1.0);

    let absorption_score = 100.0 * (sell_dominance * price_resilience).sqrt();

    let regime = if absorption_score >= 60.0 {
        "SELL_ABSORBED"
    } else if sell_pressure <= -25.0 && pre_return_30s_bps >= 10.0 {
        "BUY_DOMINANT"
    } else if sell_pressure >= 25.0 && pre_return_30s_bps <= -10.0 {
        "SELL_EFFECTIVE"
    } else {
        "MIXED"
    }
    .to_string();

    (pre_return_30s_bps, sell_pressure, absorption_score, regime)
}

fn recompute_tail_candidate_score(exit: &mut ExitRecord) {
    if exit.return_5s_bps.is_none() {
        exit.tail_candidate_score = 0.0;

        return;
    }

    let cluster_score = (20.0 * exit.cluster_unique_wallets_10s as f64
        + 10.0 * exit.cluster_sell_quote_sol_10s.ln_1p())
    .clamp(0.0, 100.0);

    exit.tail_candidate_score = (0.35 * exit.impact_score
        + 0.25 * exit.leader_quality_120s_at_entry
        + 0.20 * exit.sell_efficiency_5s_score
        + 0.10 * cluster_score
        + 0.10 * (100.0 - exit.absorption_score))
        .clamp(0.0, 100.0);
}

fn exit_return_is_set(exit: &ExitRecord, horizon_ms: u64) -> bool {
    match horizon_ms {
        500 => exit.return_500ms_bps.is_some(),

        1_000 => exit.return_1s_bps.is_some(),

        2_000 => exit.return_2s_bps.is_some(),

        3_000 => exit.return_3s_bps.is_some(),

        5_000 => exit.return_5s_bps.is_some(),

        10_000 => exit.return_10s_bps.is_some(),

        30_000 => exit.return_30s_bps.is_some(),

        60_000 => exit.return_60s_bps.is_some(),

        120_000 => exit.return_120s_bps.is_some(),

        300_000 => exit.return_300s_bps.is_some(),

        _ => false,
    }
}

fn set_exit_return(exit: &mut ExitRecord, horizon_ms: u64, value: f64) {
    match horizon_ms {
        500 => exit.return_500ms_bps = Some(value),

        1_000 => exit.return_1s_bps = Some(value),

        2_000 => exit.return_2s_bps = Some(value),

        3_000 => exit.return_3s_bps = Some(value),

        5_000 => exit.return_5s_bps = Some(value),

        10_000 => exit.return_10s_bps = Some(value),

        30_000 => exit.return_30s_bps = Some(value),

        60_000 => exit.return_60s_bps = Some(value),

        120_000 => exit.return_120s_bps = Some(value),

        300_000 => exit.return_300s_bps = Some(value),

        _ => {}
    }
}

fn fill_exit(
    exit: &mut ExitRecord,
    points: &VecDeque<PoolPoint>,
    current_ms: u64,
) -> Vec<LearningRecord> {
    let mut learned = Vec::<LearningRecord>::new();
    for horizon in HORIZONS {
        if exit.evaluated_horizons.contains(&horizon) || exit_return_is_set(exit, horizon) { continue; }
        let target = exit.event_unix_ms.saturating_add(horizon);
        if current_ms < target || current_ms < exit.signal_available_unix_ms { continue; }
        let mark = pool_price_asof(points, target, ASOF_MAX_STALENESS_MS);
        let value = mark.and_then(|(p, _)| return_bps(exit.entry_price, p));
        let asof_age_ms = mark.map(|(_, age)| age);
        exit.evaluated_horizons.push(horizon);
        if let Some(v) = value {
            set_exit_return(exit, horizon, v);
            if horizon == 5_000 {
                let response_score = (-v / 25.0 * 100.0).clamp(0.0, 100.0);
                exit.sell_efficiency_5s_score = (0.75 * response_score + 0.25 * exit.impact_score).clamp(0.0, 100.0);
                recompute_tail_candidate_score(exit);
            }
        }
        learned.push(LearningRecord {
            event_id: exit.event_id.clone(),

            symbol: exit.symbol.clone(),

            wallet: exit.wallet.clone(),

            horizon_ms: horizon,

            signal_available_unix_ms: exit.signal_available_unix_ms,

            actor_resolution_latency_ms: exit.actor_resolution_latency_ms,

            return_bps: value,

            asof_age_ms,
            status: if value.is_some() { "VALID_REFERENCE" } else { "MARK_MISSING_OR_STALE" }.to_string(),
            horizon_due_unix_ms: target,
            signal_age_ms: current_ms.saturating_sub(exit.event_unix_ms),
            publication_delay_ms: current_ms.saturating_sub(target.max(exit.signal_available_unix_ms)),
            price_basis: exit.entry_price_basis.clone(),

            matured_unix_ms: current_ms,

            regime: exit.regime.clone(),

            impact_score: exit.impact_score,

            absorption_score: exit.absorption_score,

            sell_efficiency_5s_score: exit.sell_efficiency_5s_score,

            tail_candidate_score: exit.tail_candidate_score,

            sell_quote_sol: exit.sell_quote_sol,

            sell_pool_reserve_pct: exit.sell_pool_reserve_pct,

            cluster_size: exit.cluster_size,

            cluster_unique_wallets_10s: exit.cluster_unique_wallets_10s,

            cluster_sell_quote_sol_10s: exit.cluster_sell_quote_sol_10s,

            leader_quality_120s_at_entry: exit.leader_quality_120s_at_entry,

            leader_samples_120s_at_entry: exit.leader_samples_120s_at_entry,
        });
    }

    learned
}

fn update_exit_returns(
    state: &mut EngineState,
    symbol: &str,
    current_ms: u64,
) -> Vec<LearningRecord> {
    let Some(pool) = state.pools.get(symbol) else {
        return Vec::new();
    };

    let points = &pool.events;

    let mut learned = Vec::<LearningRecord>::new();

    for exit in state.exits.iter_mut() {
        if exit.symbol != symbol || exit.evaluated_horizons.len() == HORIZONS.len() {
            continue;
        }

        learned.extend(fill_exit(exit, points, current_ms));
    }

    learned
}

fn update_swap_state(
    state: &mut EngineState,
    event: &SwapEvent,
    decode_us: f64,
) -> Vec<LearningRecord> {
    state.latest_slot = state.latest_slot.max(event.slot);

    state.swaps_total += 1;

    state.decode_samples_us.push_back(decode_us);

    while state.decode_samples_us.len() > 2048 {
        state.decode_samples_us.pop_front();
    }

    if let Some(pool) = state.pools.get_mut(&event.symbol) {
        pool.swaps_total += 1;

        pool.last_meme_reserve = event.pool_meme_reserve;

        pool.last_quote_reserve_sol = event.pool_quote_reserve_sol;

        pool.last_signature = event.signature.clone();

        pool.last_event_unix_ms = event.received_unix_ms;

        pool.last_slot = event.slot;

        pool.events.push_back(PoolPoint {
            ts_ms: event.received_unix_ms,

            price: event.reference_post().expect("reference validated before state update"),

            side: event.side,

            meme_qty: event.meme_qty,
        });

        let cutoff = event.received_unix_ms.saturating_sub(POOL_HISTORY_MS);

        while pool
            .events
            .front()
            .map(|p| p.ts_ms < cutoff)
            .unwrap_or(false)
        {
            pool.events.pop_front();
        }
    }

    let completed_learning = update_exit_returns(state, &event.symbol, now_ms().max(event.received_unix_ms));

    state.learning_records += completed_learning.len() as u64;

    while state
        .exits
        .front()
        .map(|e| event.received_unix_ms.saturating_sub(e.event_unix_ms) > EXIT_HISTORY_MS)
        .unwrap_or(false)
    {
        state.exits.pop_front();
    }
    completed_learning
}
fn update_wallet(state: &mut EngineState, wallet: &str, event: &SwapEvent) -> Option<ExitRecord> {
    let key = format!("{}|{}", event.symbol, wallet);

    let (pre_return_30s_bps, pre_sell_pressure_30s_pct, absorption_score, regime) =
        exit_regime_context(state, event);

    let (pool_sell_1s, pool_sell_5s, pool_sell_30s) =
        if let Some(pool) = state.pools.get(&event.symbol) {
            (
                pool_sell_qty(pool, event.received_unix_ms, 1_000),
                pool_sell_qty(pool, event.received_unix_ms, 5_000),
                pool_sell_qty(pool, event.received_unix_ms, 30_000),
            )
        } else {
            (0.0, 0.0, 0.0)
        };

    let (pre_inventory, wallet_sell_1s, wallet_sell_5s, wallet_sell_30s) = {
        let wallet_state = state.wallets.entry(key).or_insert_with(|| WalletState {
            wallet: wallet.to_string(),

            symbol: event.symbol.clone(),

            first_seen_ms: event.received_unix_ms,

            last_seen_ms: event.received_unix_ms,

            buys: 0,
            sells: 0,

            observed_inventory: 0.0,

            last_action: event.side,

            last_buy_ms: None,

            buy_to_sell_flips: 0,

            trades: VecDeque::new(),

            holding_samples_ms: VecDeque::new(),
        });

        let pre_inventory = wallet_state.observed_inventory;

        let previous_action = wallet_state.last_action;

        match event.side {
            Side::Buy => {
                wallet_state.buys += 1;

                wallet_state.observed_inventory += event.meme_qty;

                wallet_state.last_buy_ms = Some(event.received_unix_ms);
            }

            Side::Sell => {
                wallet_state.sells += 1;

                wallet_state.observed_inventory =
                    (wallet_state.observed_inventory - event.meme_qty).max(0.0);

                if previous_action == Side::Buy {
                    wallet_state.buy_to_sell_flips += 1;

                    if let Some(buy_ms) = wallet_state.last_buy_ms {
                        wallet_state
                            .holding_samples_ms
                            .push_back(event.received_unix_ms.saturating_sub(buy_ms));

                        while wallet_state.holding_samples_ms.len() > 64 {
                            wallet_state.holding_samples_ms.pop_front();
                        }
                    }
                }
            }
        }

        wallet_state.last_action = event.side;

        wallet_state.last_seen_ms = event.received_unix_ms;

        wallet_state.trades.push_back(TradeObs {
            ts_ms: event.received_unix_ms,

            side: event.side,

            qty: event.meme_qty,
        });

        let cutoff = event.received_unix_ms.saturating_sub(HISTORY_MS);

        while wallet_state
            .trades
            .front()
            .map(|t| t.ts_ms < cutoff)
            .unwrap_or(false)
        {
            wallet_state.trades.pop_front();
        }

        (
            pre_inventory,
            wallet_sell_qty(wallet_state, event.received_unix_ms, 1_000),
            wallet_sell_qty(wallet_state, event.received_unix_ms, 5_000),
            wallet_sell_qty(wallet_state, event.received_unix_ms, 30_000),
        )
    };

    if event.side != Side::Sell || pre_inventory <= 0.0 {
        return None;
    }

    let exit_pct = (event.meme_qty / pre_inventory * 100.0).clamp(0.0, 100.0);

    let cluster_size = 1 + state
        .exits
        .iter()
        .rev()
        .take_while(|e| event.received_unix_ms.saturating_sub(e.event_unix_ms) <= 2000)
        .filter(|e| e.symbol == event.symbol)
        .count() as u64;

    let sell_pool_reserve_pct = if event.pool_meme_reserve > 0.0 {
        event.meme_qty / event.pool_meme_reserve * 100.0
    } else {
        0.0
    };

    let observed_inventory_pool_pct = if event.pool_meme_reserve > 0.0 {
        pre_inventory / event.pool_meme_reserve * 100.0
    } else {
        0.0
    };

    let cluster_cutoff_10s = event.received_unix_ms.saturating_sub(10_000);

    let mut cluster_wallets = HashSet::<String>::new();

    cluster_wallets.insert(wallet.to_string());

    let mut cluster_sell_quote_sol_10s = event.quote_sol;

    for prior in state.exits.iter().rev() {
        if prior.event_unix_ms < cluster_cutoff_10s {
            break;
        }

        if prior.symbol != event.symbol {
            continue;
        }

        cluster_wallets.insert(prior.wallet.clone());

        cluster_sell_quote_sol_10s += prior.sell_quote_sol;
    }

    let cluster_unique_wallets_10s = cluster_wallets.len() as u64;

    let leader_entry = leader_profile(
        state,
        wallet,
        &event.symbol,
        120_000,
        Some(&regime),
        event.received_unix_ms,
    );

    Some(ExitRecord {
        event_id: format!("{}|{}|{}", event.signature, wallet, event.received_unix_ms),

        symbol: event.symbol.clone(),

        wallet: wallet.to_string(),

        event_unix_ms: event.received_unix_ms,

        signal_available_unix_ms: 0,

        actor_resolution_latency_ms: 0,

        entry_price: event.reference_post()?,
        observed_entry_price: Some(event.price_sol_per_meme),
        entry_price_basis: event.reference_basis().to_string(),
        evaluated_horizons: Vec::new(),

        inventory_before: pre_inventory,

        sell_qty: event.meme_qty,

        inventory_exit_pct: exit_pct,

        cluster_size,

        sell_quote_sol: event.quote_sol,

        sell_pool_reserve_pct,

        observed_inventory_pool_pct,

        wallet_sell_flow_share_1s_pct: flow_share_pct(wallet_sell_1s, pool_sell_1s),

        wallet_sell_flow_share_5s_pct: flow_share_pct(wallet_sell_5s, pool_sell_5s),

        wallet_sell_flow_share_30s_pct: flow_share_pct(wallet_sell_30s, pool_sell_30s),

        cluster_unique_wallets_10s,

        cluster_sell_quote_sol_10s,

        pre_return_30s_bps,

        pre_sell_pressure_30s_pct,

        absorption_score,

        sell_efficiency_5s_score: 0.0,

        regime,

        leader_quality_120s_at_entry: leader_entry.quality_score,

        leader_samples_120s_at_entry: leader_entry.samples,

        tail_candidate_score: 0.0,

        impact_score: 0.0,

        return_500ms_bps: None,

        return_1s_bps: None,

        return_2s_bps: None,

        return_3s_bps: None,

        return_5s_bps: None,

        return_10s_bps: None,

        return_30s_bps: None,

        return_60s_bps: None,

        return_120s_bps: None,

        return_300s_bps: None,
    })
}

fn adaptive_impact_score(state: &EngineState, candidate: &ExitRecord) -> f64 {
    let mut reserve = state
        .exits
        .iter()
        .filter(|e| e.symbol == candidate.symbol)
        .map(|e| e.sell_pool_reserve_pct)
        .collect::<Vec<_>>();

    let mut absolute_sell = state
        .exits
        .iter()
        .filter(|e| e.symbol == candidate.symbol)
        .map(|e| e.sell_quote_sol)
        .collect::<Vec<_>>();

    let mut cluster_quote = state
        .exits
        .iter()
        .filter(|e| e.symbol == candidate.symbol)
        .map(|e| e.cluster_sell_quote_sol_10s)
        .collect::<Vec<_>>();

    let mut cluster_wallets = state
        .exits
        .iter()
        .filter(|e| e.symbol == candidate.symbol)
        .map(|e| e.cluster_unique_wallets_10s as f64)
        .collect::<Vec<_>>();

    let mut flow = state
        .exits
        .iter()
        .filter(|e| e.symbol == candidate.symbol)
        .map(|e| e.wallet_sell_flow_share_5s_pct)
        .collect::<Vec<_>>();

    reserve.push(candidate.sell_pool_reserve_pct);

    absolute_sell.push(candidate.sell_quote_sol);

    cluster_quote.push(candidate.cluster_sell_quote_sol_10s);

    cluster_wallets.push(candidate.cluster_unique_wallets_10s as f64);

    flow.push(candidate.wallet_sell_flow_share_5s_pct);

    // R5C: inventory_exit_pct no longer contributes to impact.
    // The 6h forensic window showed that observed inventory is
    // capture-local and can falsely make dust exits look decisive.
    let raw = 0.30 * percentile_rank(&reserve, candidate.sell_pool_reserve_pct)
        + 0.30 * percentile_rank(&absolute_sell, candidate.sell_quote_sol)
        + 0.20 * percentile_rank(&cluster_quote, candidate.cluster_sell_quote_sol_10s)
        + 0.15
            * percentile_rank(
                &cluster_wallets,
                candidate.cluster_unique_wallets_10s as f64,
            )
        + 0.05 * percentile_rank(&flow, candidate.wallet_sell_flow_share_5s_pct);

    let evidence = (reserve.len() as f64 / 8.0).clamp(0.0, 1.0);

    (raw * evidence).clamp(0.0, 100.0)
}

fn account_keys(tx: &Value) -> Vec<String> {
    let mut out = Vec::<String>::new();

    if let Some(keys) = tx
        .pointer("/transaction/message/accountKeys")
        .and_then(Value::as_array)
    {
        for value in keys {
            if let Some(s) = value.as_str() {
                out.push(s.to_string());
            } else if let Some(s) = value.get("pubkey").and_then(Value::as_str) {
                out.push(s.to_string());
            }
        }
    }

    for group in ["writable", "readonly"] {
        let pointer = format!("/meta/loadedAddresses/{group}");

        if let Some(keys) = tx.pointer(&pointer).and_then(Value::as_array) {
            for value in keys {
                if let Some(s) = value.as_str() {
                    out.push(s.to_string());
                }
            }
        }
    }

    out
}

fn signer_set(tx: &Value, keys: &[String]) -> HashSet<String> {
    let count = tx
        .pointer("/transaction/message/header/numRequiredSignatures")
        .and_then(Value::as_u64)
        .unwrap_or(0) as usize;

    keys.iter().take(count).cloned().collect()
}

fn inspect_ix(
    ix: &Value,
    keys: &[String],
    signers: &HashSet<String>,
    pool: &str,
) -> Option<(String, bool)> {
    let program_index = ix.get("programIdIndex")?.as_u64()? as usize;
    let program=keys.get(program_index)?.as_str();
    let accounts=ix.get("accounts")?.as_array()?;
    let indexes:Option<Vec<usize>>=accounts.iter().map(|v|v.as_u64().map(|i|i as usize)).collect();
    let indexes=indexes?;
    let decoded=bs58::decode(ix.get("data")?.as_str()?).into_vec().ok()?;
    let (pool_offset, owner_offset)=match program {
        RAYDIUM_AMM_V4=>{
            if !matches!(decoded.first(),Some(9)|Some(11)|Some(16)|Some(17)){return None;}
            (1,indexes.len().checked_sub(1)?)
        },
        raydium_protocols::CPMM=>{
            const INPUT:[u8;8]=[143, 190, 90, 218, 196, 30, 51, 222];
            const OUTPUT:[u8;8]=[55, 217, 98, 86, 163, 74, 180, 173];
            if decoded.len()<8 || (decoded[..8]!=INPUT && decoded[..8]!=OUTPUT){return None;}
            (3,0)
        },
        raydium_protocols::CLMM=>{
            const SWAP:[u8;8]=[248, 198, 158, 145, 225, 117, 135, 200];
            const SWAP2:[u8;8]=[43, 4, 237, 11, 26, 201, 30, 98];
            if decoded.len()<8 || (decoded[..8]!=SWAP && decoded[..8]!=SWAP2){return None;}
            (2,0)
        },
        _=>return None
    };
    if keys.get(*indexes.get(pool_offset)?)?!=pool{return None;}
    let owner=keys.get(*indexes.get(owner_offset)?)?.clone();
    let direct=signers.contains(&owner);

    Some((owner, direct))
}

fn parse_actor(tx: &Value, pool: &str) -> ActorResolution {
    let keys = account_keys(tx);

    if keys.is_empty() {
        return ActorResolution {
            quality: ActorQuality::Ambiguous,
        };
    }

    let signers = signer_set(tx, &keys);

    let mut hits = Vec::<(String, bool)>::new();

    if let Some(ixs) = tx
        .pointer("/transaction/message/instructions")
        .and_then(Value::as_array)
    {
        for ix in ixs {
            if let Some(hit) = inspect_ix(ix, &keys, &signers, pool) {
                hits.push(hit);
            }
        }
    }

    if let Some(groups) = tx
        .pointer("/meta/innerInstructions")
        .and_then(Value::as_array)
    {
        for group in groups {
            if let Some(ixs) = group.get("instructions").and_then(Value::as_array) {
                for ix in ixs {
                    if let Some(hit) = inspect_ix(ix, &keys, &signers, pool) {
                        hits.push(hit);
                    }
                }
            }
        }
    }

    let direct = hits
        .iter()
        .filter(|(_, direct)| *direct)
        .map(|(owner, _)| owner.clone())
        .collect::<HashSet<_>>();

    if direct.len() == 1 {
        return ActorResolution {
            quality: ActorQuality::Direct(direct.into_iter().next().unwrap()),
        };
    }

    let all = hits
        .iter()
        .map(|(owner, _)| owner.clone())
        .collect::<HashSet<_>>();

    if direct.is_empty() && all.len() == 1 {
        return ActorResolution {
            quality: ActorQuality::Routed,
        };
    }

    ActorResolution {
        quality: ActorQuality::Ambiguous,
    }
}

async fn fetch_actor(
    client: &Client,
    rpc: &str,
    event: &SwapEvent,
) -> Result<ActorResolution, String> {
    if let Some(hint)=&event.actor_hint {return Ok(hint.clone());}
    let body = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            event.signature,
            {
                "encoding": "json",
                "commitment": "confirmed",
                "maxSupportedTransactionVersion": 1
            }
        ]
    });

    let mut last_error = String::new();

    for attempt in 1..=3 {
        let response = client.post(rpc).json(&body).send().await;

        match response {
            Ok(response) => match response.error_for_status() {
                Ok(response) => match response.json::<Value>().await {
                    Ok(value) => {
                        if let Some(error) = value.get("error") {
                            last_error = error.to_string();
                        } else if let Some(tx) = value.get("result") {
                            if !tx.is_null() {
                                return Ok(parse_actor(tx, &event.pool));
                            }

                            last_error = "null result".to_string();
                        }
                    }

                    Err(e) => {
                        last_error = format!("json: {e}");
                    }
                },

                Err(e) => {
                    last_error = format!("http: {e}");
                }
            },

            Err(e) => {
                last_error = format!("transport: {e}");
            }
        }

        if attempt < 3 {
            tokio::time::sleep(Duration::from_millis(700 * attempt)).await;
        }
    }

    Err(last_error)
}

fn pool_metric(state: &EngineState, symbol: &str, now: u64) -> PoolMetric {
    let Some(pool) = state.pools.get(symbol) else {
        return PoolMetric {
            symbol: symbol.to_string(),

            price: 0.0,
            return_1m_bps: 0.0,
            swaps_per_min: 0.0,
            buy_sell_imbalance: 0.0,
            unique_wallets_5m: 0,
            direct_actor_pct: 0.0,
            quality_quarantined: 0,
            median_interarrival_ms: 0.0,
        };
    };

    let last_price = pool.events.back().map(|p| p.price).unwrap_or(0.0);

    let cutoff_60 = now.saturating_sub(60_000);

    let last60 = pool
        .events
        .iter()
        .filter(|p| p.ts_ms >= cutoff_60)
        .collect::<Vec<_>>();

    let buys = last60.iter().filter(|p| p.side == Side::Buy).count();

    let sells = last60.len().saturating_sub(buys);

    let imbalance = if last60.is_empty() {
        0.0
    } else {
        (buys as f64 - sells as f64) / last60.len() as f64
    };

    let return_1m = last60
        .first()
        .and_then(|first| return_bps(first.price, last_price))
        .unwrap_or(0.0);

    let mut intervals = Vec::<f64>::new();

    for pair in pool.events.as_slices().0.windows(2) {
        intervals.push(pair[1].ts_ms.saturating_sub(pair[0].ts_ms) as f64);
    }

    let mut intervals2 = intervals;

    let median_dt = median(&mut intervals2);

    let unique_wallets = state
        .wallets
        .values()
        .filter(|w| w.symbol == symbol)
        .filter(|w| now.saturating_sub(w.last_seen_ms) <= HISTORY_MS)
        .count() as u64;

    let direct_pct = if pool.actor_resolved > 0 {
        100.0 * pool.direct_actor as f64 / pool.actor_resolved as f64
    } else {
        0.0
    };

    PoolMetric {
        symbol: symbol.to_string(),

        price: last_price,

        return_1m_bps: return_1m,

        swaps_per_min: last60.len() as f64,

        buy_sell_imbalance: imbalance,

        unique_wallets_5m: unique_wallets,

        direct_actor_pct: direct_pct,

        quality_quarantined: pool.quality_quarantined,

        median_interarrival_ms: median_dt,
    }
}

fn p95(values: &VecDeque<f64>) -> f64 {
    if values.is_empty() {
        return 0.0;
    }

    let mut v = values.iter().copied().collect::<Vec<_>>();

    v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let index = ((v.len() - 1) as f64 * 0.95).round() as usize;

    v[index]
}

fn build_dashboard(state: &EngineState, queue_depth: u64, now: u64) -> DashboardState {
    let (mut wallets, scores, bot_like) = score_wallets(state, now);

    wallets.truncate(100);

    let pool_metrics = POOL_SYMBOLS
        .iter()
        .filter(|symbol| state.pools.contains_key(**symbol))
        .map(|symbol| pool_metric(state, symbol, now))
        .collect::<Vec<_>>();

    let swaps_per_min = pool_metrics.iter().map(|p| p.swaps_per_min).sum::<f64>();

    let direct_actor_pct = if state.actor_rpc_ok > 0 {
        100.0 * state.direct_actors as f64 / state.actor_rpc_ok as f64
    } else {
        0.0
    };

    let mut exits = state
        .exits
        .iter()
        .rev()
        .take(100)
        .map(|e| {
            let key = format!("{}|{}", e.symbol, e.wallet);

            let (auto, fast) = scores.get(&key).copied().unwrap_or((0.0, 0.0));

            DashboardExit {
                symbol: e.symbol.clone(),

                wallet: e.wallet.clone(),

                age_ms: now.saturating_sub(e.event_unix_ms),

                signal_available_unix_ms: e.signal_available_unix_ms,

                actor_resolution_latency_ms: e.actor_resolution_latency_ms,

                inventory_exit_pct: e.inventory_exit_pct,

                cluster_size: e.cluster_size,

                automation_score: auto,

                fast_money_score: fast,

                impact_score: e.impact_score,

                sell_quote_sol: e.sell_quote_sol,

                sell_pool_reserve_pct: e.sell_pool_reserve_pct,

                wallet_sell_flow_share_1s_pct: e.wallet_sell_flow_share_1s_pct,

                wallet_sell_flow_share_5s_pct: e.wallet_sell_flow_share_5s_pct,

                wallet_sell_flow_share_30s_pct: e.wallet_sell_flow_share_30s_pct,

                cluster_unique_wallets_10s: e.cluster_unique_wallets_10s,

                cluster_sell_quote_sol_10s: e.cluster_sell_quote_sol_10s,

                pre_return_30s_bps: e.pre_return_30s_bps,

                pre_sell_pressure_30s_pct: e.pre_sell_pressure_30s_pct,

                absorption_score: e.absorption_score,

                sell_efficiency_5s_score: e.sell_efficiency_5s_score,

                regime: e.regime.clone(),

                leader_quality_120s_at_entry: e.leader_quality_120s_at_entry,

                leader_samples_120s_at_entry: e.leader_samples_120s_at_entry,

                tail_candidate_score: e.tail_candidate_score,

                return_500ms_bps: e.return_500ms_bps,

                return_1s_bps: e.return_1s_bps,

                return_2s_bps: e.return_2s_bps,

                return_5s_bps: e.return_5s_bps,

                return_10s_bps: e.return_10s_bps,

                return_30s_bps: e.return_30s_bps,

                return_60s_bps: e.return_60s_bps,

                return_120s_bps: e.return_120s_bps,

                return_300s_bps: e.return_300s_bps,

                status: "SHADOW".to_string(),
            }
        })
        .collect::<Vec<_>>();

    exits.reverse();

    let active_exits = state
        .exits
        .iter()
        .filter(|e| now.saturating_sub(e.event_unix_ms) <= 30_000)
        .count() as u64;

    DashboardState {
        schema: "belial_r5c2_dashboard_state_v2",

        mode: "LIVE_READ_ONLY",

        engine_status: state.engine_status.clone(),

        capture_elapsed_secs: now.saturating_sub(state.started_ms) / 1000,

        updated_unix_ms: now,

        slot: state.latest_slot,

        swaps_total: state.swaps_total,

        swaps_per_min,

        direct_actor_pct,

        unique_wallets: state.wallets.len() as u64,

        bot_like_candidates: bot_like,

        active_exit_candidates: active_exits,

        actor_queue_depth: queue_depth,

        rpc_errors: state.rpc_errors,

        invalid_signature_quarantined: state.invalid_signature_quarantined,

        quality_quarantined: state
            .pools
            .values()
            .map(|pool| pool.quality_quarantined)
            .sum(),

        learning_records: state.learning_records,

        dropped_events: state.dropped_events,

        decode_p95_us: p95(&state.decode_samples_us),

        pools: pool_metrics,

        wallets,

        exits,

        history: state.history.iter().cloned().collect(),
    }
}

fn atomic_json(path: &Path, value: &impl Serialize) -> Result<(), Box<dyn Error>> {
    let parent = path.parent().ok_or("state parent ausente")?;

    fs::create_dir_all(parent)?;

    let tmp = path.with_extension("json.tmp");

    let bytes = serde_json::to_vec(value)?;

    fs::write(&tmp, bytes)?;

    fs::rename(&tmp, path)?;

    Ok(())
}

fn history_tick(state: &mut EngineState, now: u64) {
    let fart = pool_metric(state, "FARTCOIN", now);

    let bome = pool_metric(state, "BOME", now);

    let wif = pool_metric(state, "WIF", now);

    let cutoff = now.saturating_sub(5000);

    let mut buys = 0usize;

    let mut sells = 0usize;

    for pool in state.pools.values() {
        for event in pool.events.iter() {
            if event.ts_ms < cutoff {
                continue;
            }

            match event.side {
                Side::Buy => buys += 1,

                Side::Sell => sells += 1,
            }
        }
    }

    let total = buys + sells;

    let sell_pressure = if total > 0 {
        100.0 * sells as f64 / total as f64
    } else {
        0.0
    };

    let exits = state
        .exits
        .iter()
        .filter(|e| e.event_unix_ms >= cutoff)
        .count() as f64;

    state.history.push_back(HistoryPoint {
        t_secs: now.saturating_sub(state.started_ms) as f64 / 1000.0,

        fartcoin_ret_bps: fart.return_1m_bps,

        bome_ret_bps: bome.return_1m_bps,

        wif_ret_bps: wif.return_1m_bps,

        sell_pressure,

        exit_count: exits,
    });

    while state.history.len() > 1800 {
        state.history.pop_front();
    }
}

// Dedicated OS thread: no fsync on the Tokio runtime and no fsync per event.
fn writer_task(evidence: PathBuf, rx: std::sync::mpsc::Receiver<LogRecord>) -> Result<WriterStats, String> {
    fn run(evidence: PathBuf, rx: std::sync::mpsc::Receiver<LogRecord>) -> std::io::Result<WriterStats> {
        let mut files = Vec::<File>::new();
        for name in ["swaps.jsonl","actors.jsonl","exits.jsonl","learning_ledger.jsonl","system_events.jsonl","errors.jsonl"] {
            files.push(OpenOptions::new().create_new(true).write(true).open(evidence.join(name))?);
        }
        let mut dirty = [false; 6];
        let mut last_sync = Instant::now();
        let mut stats = WriterStats::default();
        loop {
            let mut done = false;
            match rx.recv_timeout(Duration::from_millis(100)) {
                Ok(record) => {
                    let (idx,line) = match record {
                        LogRecord::Swap(x)=>(0,x), LogRecord::Actor(x)=>(1,x), LogRecord::Exit(x)=>(2,x),
                        LogRecord::Learning(x)=>(3,x), LogRecord::System(x)=>(4,x), LogRecord::Error(x)=>(5,x),
                    };
                    let mut value: Value = serde_json::from_str(&line).map_err(|e|std::io::Error::new(std::io::ErrorKind::InvalidData,e))?;
                    if !value.is_object() {return Err(std::io::Error::new(std::io::ErrorKind::InvalidData,"log object required"));}
                    let writer_ns = now_ns();
                    value["writer_observed_unix_ns"] = json!(writer_ns);
                    value["writer_observed_unix_ms"] = json!(writer_ns / 1_000_000);
                    if let Some(received_ns) = value.get("received_unix_ns").and_then(Value::as_u64) {
                        if received_ns > 0 && writer_ns >= received_ns {
                            value["collector_to_writer_us"] = json!((writer_ns - received_ns) as f64 / 1_000.0);
                        }
                    }
                    let encoded=value.to_string();
                    writeln!(files[idx],"{encoded}")?;
                    stats.rows += 1; stats.bytes += encoded.len() as u64 + 1; dirty[idx]=true;
                },
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {},
                Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => done = true,
            }
            if done || last_sync.elapsed() >= Duration::from_secs(1) {
                for (idx,f) in files.iter().enumerate() { if dirty[idx] { f.sync_data()?; stats.sync_calls+=1; dirty[idx]=false; } }
                last_sync = Instant::now();
            }
            if done { return Ok(stats); }
        }
    }
    run(evidence,rx).map_err(|e|e.to_string())
}

async fn actor_worker(
    state: Arc<Mutex<EngineState>>,

    mut rx: mpsc::Receiver<SwapEvent>,

    queue_depth: Arc<AtomicU64>,

    client: Client,

    rpc: String,

    log_tx: LogSink,
) {
    let mut cache = HashMap::<String, ActorResolution>::new();

    let mut priority_buffer = VecDeque::<SwapEvent>::new();

    loop {
        if priority_buffer.is_empty() {
            match rx.recv().await {
                Some(event) => {
                    priority_buffer.push_back(event);
                }

                None => break,
            }
        }

        while priority_buffer.len() < ACTOR_PRIORITY_BUFFER {
            match rx.try_recv() {
                Ok(event) => {
                    priority_buffer.push_back(event);
                }

                Err(_) => break,
            }
        }

        let priority_index = priority_buffer
            .iter()
            .position(|event| event.side == Side::Sell)
            .unwrap_or(0);

        let event = priority_buffer
            .remove(priority_index)
            .expect("actor priority buffer");

        queue_depth.fetch_sub(1, Ordering::Relaxed);

        {
            let mut guard = state.lock().unwrap();

            guard.actor_attempts += 1;
        }

        let cache_key = format!("{}|{}", event.signature, event.pool);

        let resolution = if let Some(value) = cache.get(&cache_key).cloned() {
            Ok(value)
        } else {
            let result = fetch_actor(&client, &rpc, &event).await;

            if let Ok(value) = &result {
                cache.insert(cache_key, value.clone());
            }

            result
        };

        match resolution {
            Ok(resolution) => {
                let resolved_unix_ms = now_ms();

                let actor_resolution_latency_ms =
                    resolved_unix_ms.saturating_sub(event.received_unix_ms);

                let mut actor_json = json!({
                    "symbol":
                        event.symbol,

                    "signature":
                        event.signature,

                    "slot":
                        event.slot,

                    "received_unix_ms":
                        event.received_unix_ms,

                    "resolved_unix_ms":
                        resolved_unix_ms,

                    "actor_resolution_latency_ms":
                        actor_resolution_latency_ms,

                    "side":
                        event.side.label(),

                    "actor_queue_priority":
                        if event.side == Side::Sell {
                            "HIGH_SELL"
                        } else {
                            "BACKGROUND_BUY"
                        },

                    "meme_qty":
                        event.meme_qty,

                    "price_sol_per_meme":
                        event.price_sol_per_meme
                });

                let mut exit_created: Option<ExitRecord> = None;

                {
                    let mut guard = state.lock().unwrap();

                    guard.actor_rpc_ok += 1;

                    if let Some(pool) = guard.pools.get_mut(&event.symbol) {
                        pool.actor_resolved += 1;
                    }

                    match resolution.quality {
                        ActorQuality::Direct(wallet) => {
                            guard.direct_actors += 1;

                            if let Some(pool) = guard.pools.get_mut(&event.symbol) {
                                pool.direct_actor += 1;
                            }

                            exit_created = update_wallet(&mut guard, &wallet, &event);

                            actor_json["quality"] = json!("DIRECT_SIGNER");

                            actor_json["wallet"] = json!(wallet);

                            if let Some(mut exit) = exit_created.clone() {
                                exit.signal_available_unix_ms = resolved_unix_ms;

                                exit.actor_resolution_latency_ms = actor_resolution_latency_ms;

                                exit.impact_score = adaptive_impact_score(&guard, &exit);

                                let _ = log_tx.send(LogRecord::Exit(serde_json::to_string(&exit).expect("exit serialize")));
                                let learned = if let Some(pool) = guard.pools.get(&event.symbol) {
                                    fill_exit(&mut exit, &pool.events, now_ms().max(resolved_unix_ms))
                                } else { Vec::new() };
                                guard.learning_records += learned.len() as u64;
                                for row in learned {
                                    let _ = log_tx.send(LogRecord::Learning(serde_json::to_string(&row).expect("label serialize")));
                                }
                                guard.exits.push_back(exit);
                                exit_created = None;
                            }
                        }

                        ActorQuality::Routed => {
                            guard.routed_actors += 1;

                            actor_json["quality"] = json!("ROUTED_SHADOW_ONLY");
                        }

                        ActorQuality::Ambiguous => {
                            actor_json["quality"] = json!("AMBIGUOUS");
                        }
                    }
                }

                let _ = log_tx.send(LogRecord::Actor(actor_json.to_string()));

                if let Some(exit) = exit_created {
                    let _ = log_tx.send(LogRecord::Exit(
                        serde_json::to_string(&exit).unwrap_or_default(),
                    ));
                }
            }

            Err(error) => {
                {
                    let mut guard = state.lock().unwrap();

                    guard.rpc_errors += 1;
                }

                let _ = log_tx.send(LogRecord::Error(
                    json!({
                        "kind":
                            "actor_rpc",

                        "symbol":
                            event.symbol,

                        "signature":
                            event.signature,

                        "error":
                            error,

                        "unix_ms":
                            now_ms()
                    })
                    .to_string(),
                ));
            }
        }
    }
}

async fn pool_task(
    cfg: PoolCfg,
    meta: PoolMeta,

    ws_url: String,
    client: Client,
    rpc: String,
    config_fresh: Option<Arc<AtomicU64>>,

    state: Arc<Mutex<EngineState>>,

    actor_tx: mpsc::Sender<SwapEvent>,

    queue_depth: Arc<AtomicU64>,

    log_tx: LogSink,
) {
    let mut disconnected_at: Option<u64> = None;
    let mut generation: u64 = 0;
    let health_path = std::env::var("BELIAL_A1_HEALTH_FILE").ok().map(PathBuf::from);
    let health = |status: &str, generation: u64| {
        if let Some(path) = &health_path {
            if let Err(error) = atomic_json(path, &json!({"state":status,"at_ms":now_ms(),"generation":generation,
                "symbol":cfg.symbol,"subscription_ack_required":true,"source":"NATIVE_WS"})) {
                eprintln!("A1_HEALTH_IO_ERROR={error}");
            }
        }
    };
    let mut accepted_signatures=HashSet::<String>::new();
    let mut signature_order=VecDeque::<String>::new();

    loop {
        health("CONNECTING", generation);
        let connection = connect_async(&ws_url).await;

        let (mut ws, _) = match connection {
            Ok(v) => v,

            Err(error) => {
                health("DISCONNECTED", generation);
                generation = generation.saturating_add(1);
                let _ = log_tx.send(LogRecord::Error(
                    json!({
                        "kind":
                            "ws_connect",

                        "symbol":
                            cfg.symbol,

                        "error":
                            error.to_string(),

                        "unix_ms":
                            now_ms()
                    })
                    .to_string(),
                ));

                if disconnected_at.is_none() {
                    disconnected_at = Some(now_ms());
                }

                tokio::time::sleep(Duration::from_secs(2)).await;

                continue;
            }
        };

        let request = json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {
                    "mentions": [
                        cfg.pool
                    ]
                },
                {
                    "commitment":
                        "confirmed"
                }
            ]
        });

        if ws
            .send(Message::Text(request.to_string().into()))
            .await
            .is_err()
        {
            disconnected_at = Some(now_ms());
            health("DISCONNECTED", generation); generation = generation.saturating_add(1);
            continue;
        }

        let connected_ms = now_ms();

        let outage_ms = disconnected_at
            .map(|ts| connected_ms.saturating_sub(ts))
            .unwrap_or(0);

        let watermark = {
            let guard = state.lock().unwrap();

            guard
                .pools
                .get(&cfg.symbol)
                .map(|pool| {
                    (
                        pool.last_signature.clone(),
                        pool.last_slot,
                        pool.last_event_unix_ms,
                    )
                })
                .unwrap_or_default()
        };

        let _ = log_tx.send(LogRecord::System(
            json!({
                "kind": "ws_connected",
                "symbol": cfg.symbol,
                "unix_ms": connected_ms,
                "outage_ms": outage_ms,
                "watermark_signature": watermark.0,
                "watermark_slot": watermark.1,
                "watermark_event_unix_ms": watermark.2
            })
            .to_string(),
        ));

        disconnected_at = None;

        let mut missed_keepalives = 0u32;
        let mut acknowledged = false;
        let mut last_health_ms = 0u64;
        health("WAITING_ACK", generation);

        let mut disconnect_reason = "stream_ended".to_string();

        loop {
            let next_message =
                tokio::time::timeout(Duration::from_millis(WS_IDLE_PING_AFTER_MS), ws.next()).await;

            let message = match next_message {
                Ok(Some(Ok(message))) => {
                    missed_keepalives = 0;
                    if acknowledged && now_ms().saturating_sub(last_health_ms)>=1000 {
                        health("ACTIVE", generation); last_health_ms=now_ms();
                    }
                    message
                }

                Ok(Some(Err(error))) => {
                    disconnect_reason = format!("stream_error:{error}");

                    break;
                }

                Ok(None) => {
                    disconnect_reason = "stream_closed".to_string();

                    break;
                }

                Err(_) => {
                    missed_keepalives += 1;

                    if missed_keepalives >= WS_KEEPALIVE_MAX_MISSES {
                        disconnect_reason = "keepalive_timeout".to_string();

                        let _ = log_tx.send(LogRecord::System(
                            json!({
                                "kind": "ws_keepalive_timeout",
                                "symbol": cfg.symbol,
                                "misses": missed_keepalives,
                                "unix_ms": now_ms()
                            })
                            .to_string(),
                        ));

                        break;
                    }

                    let payload = now_ms().to_be_bytes().to_vec();

                    if let Err(error) = ws.send(Message::Ping(payload.into())).await {
                        disconnect_reason = format!("keepalive_send_error:{error}");

                        break;
                    }

                    let _ = log_tx.send(LogRecord::System(
                        json!({
                            "kind": "ws_keepalive_ping",
                            "symbol": cfg.symbol,
                            "miss": missed_keepalives,
                            "unix_ms": now_ms()
                        })
                        .to_string(),
                    ));

                    continue;
                }
            };

            let text = match message {
                Message::Text(text) => text,

                Message::Ping(_) => {
                    // Tungstenite queues the RFC-compliant automatic
                    // Pong when the Ping is read. Flush it immediately
                    // because this socket can otherwise be read-only for
                    // long stretches.
                    if let Err(error) = ws.flush().await {
                        disconnect_reason = format!("pong_flush_error:{error}");

                        break;
                    }

                    continue;
                }

                Message::Pong(_) => continue,

                Message::Close(frame) => {
                    disconnect_reason = format!("remote_close:{frame:?}");

                    break;
                }

                _ => continue,
            };

            let notification_start = Instant::now();

            let Ok(value) = serde_json::from_str::<Value>(text.as_str()) else {
                continue;
            };

            if value.get("id").and_then(Value::as_u64) == Some(1) {
                if value.get("result").and_then(Value::as_u64).is_some() {
                    acknowledged = true; health("ACTIVE",generation); last_health_ms=now_ms();
                } else { disconnect_reason="subscription_rejected".to_string(); break; }
                continue;
            }
            if a1_scope() && !acknowledged { continue; }
            let Some(result) = value.pointer("/params/result") else {
                continue;
            };

            let slot = result
                .pointer("/context/slot")
                .and_then(Value::as_u64)
                .unwrap_or(0);

            let tx_value = result.get("value");

            let Some(tx_value) = tx_value else {
                continue;
            };

            if !tx_value.get("err").map(Value::is_null).unwrap_or(false) {
                continue;
            }

            let Some(signature) = tx_value.get("signature").and_then(Value::as_str) else {
                continue;
            };

            if placeholder_signature(signature) {
                {
                    let mut guard = state.lock().unwrap();

                    guard.invalid_signature_quarantined += 1;
                }

                let _ = log_tx.send(LogRecord::System(
                    json!({
                        "kind": "invalid_signature_quarantine",
                        "symbol": cfg.symbol,
                        "signature": signature,
                        "slot": slot,
                        "unix_ms": now_ms()
                    })
                    .to_string(),
                ));

                continue;
            }

            let Some(logs) = tx_value.get("logs").and_then(Value::as_array) else {
                continue;
            };

            if accepted_signatures.contains(signature) {continue;}
            let log_received_ns=now_ns();
            let raylogs=match raydium_protocols::payloads(logs,&cfg.owner,&cfg.pool) {
                Ok(v)=>v,
                Err(error)=>{
                    state.lock().unwrap().decode_errors+=1;
                    let _=log_tx.send(LogRecord::Error(json!({"kind":"protocol_log_scope", "symbol":cfg.symbol,
                        "signature":signature,"error":error,"unix_ms":now_ms()}).to_string()));
                    continue;
                }
            };

            if raylogs.is_empty() {
                continue;
            }

            if raylogs.len() != 1 {
                let mut guard = state.lock().unwrap();

                if let Some(pool) = guard.pools.get_mut(&cfg.symbol) {
                    pool.multi_raylog_quarantined += 1;
                }

                continue;
            }

            if let Some(fresh)=&config_fresh {
                let checked=fresh.load(Ordering::Relaxed);
                if checked==0 || now_ms().saturating_sub(checked)>15_000 {
                    let mut guard=state.lock().unwrap();if let Some(pool)=guard.pools.get_mut(&cfg.symbol){pool.quality_quarantined+=1;}
                    let _=log_tx.send(LogRecord::System(json!({"kind":"cpmm_fee_config_unknown_swap_quarantined",
                        "symbol":cfg.symbol,"signature":signature,"unix_ms":now_ms()}).to_string()));
                    continue;
                }
            }
            let tx=if cfg.owner==raydium_protocols::CLMM {
                match raydium_protocols::fetch_tx(&client,&rpc,signature).await {
                    Ok(v)=>Some(v),
                    Err(error)=>{
                        state.lock().unwrap().rpc_errors+=1;
                        let _=log_tx.send(LogRecord::Error(json!({"kind":"clmm_transaction_validation", "symbol":cfg.symbol,
                            "signature":signature,"error":error,"unix_ms":now_ms()}).to_string()));
                        continue;
                    }
                }
            } else {None};
            // Availability timestamp is AFTER any required RPC, never backdated.
            let received_ns = now_ns();
            let received = received_ns / 1_000_000;
            let decoded=if cfg.owner==RAYDIUM_AMM_V4 {
                decode_raylog(&raylogs[0],&cfg,&meta,signature,slot,received,received_ns)
            } else {
                raydium_protocols::decode(&raylogs[0],&cfg,&meta,signature,slot,received,received_ns,tx.as_ref())
            };
            let mut event=match decoded {
                Ok(v)=>v,
                Err(error)=>{
                    state.lock().unwrap().decode_errors+=1;
                    let _=log_tx.send(LogRecord::Error(json!({"kind":"protocol_decode", "symbol":cfg.symbol,
                        "signature":signature,"error":error,"unix_ms":received}).to_string()));
                    continue;
                }
            };
            if let Some(p)=event.protocol.as_mut() {
                p["log_received_unix_ns"]=json!(log_received_ns);
                p["validation_wait_us"]=json!(received_ns.saturating_sub(log_received_ns) as f64/1000.0);
                p["causal_availability_basis"]=json!("RECEIVE_AFTER_REQUIRED_RPC_VALIDATION");
                p["same_transaction_rpc_reused_for_actor"]=json!(tx.is_some());
                if let Some(fresh)=&config_fresh {p["fee_config_last_verified_unix_ms"]=json!(fresh.load(Ordering::Relaxed));}
            }

            let (quality_error, previous_watermark) = {
                let mut guard = state.lock().unwrap();

                let Some(pool) = guard.pools.get_mut(&cfg.symbol) else {
                    continue;
                };

                let watermark = (
                    pool.last_signature.clone(),
                    pool.last_slot,
                    pool.last_event_unix_ms,
                );

                let quality = validate_event_quality(pool, &event).err();

                if quality.is_some() {
                    pool.quality_quarantined += 1;
                }

                (quality, watermark)
            };

            if let Some(reason) = quality_error {
                let _ = log_tx.send(LogRecord::Error(
                    json!({
                        "kind": "price_quality_quarantine",
                        "symbol": cfg.symbol,
                        "signature": signature,
                        "slot": slot,
                        "reason": reason,
                        "price_sol_per_meme": event.price_sol_per_meme,
                        "pool_meme_reserve": event.pool_meme_reserve,
                        "pool_quote_reserve_sol": event.pool_quote_reserve_sol,
                        "unix_ms": received
                    })
                    .to_string(),
                ));

                continue;
            }

            if previous_watermark.2 > 0 {
                let gap_ms = received.saturating_sub(previous_watermark.2);

                if gap_ms > 60_000 {
                    let _ = log_tx.send(LogRecord::System(
                        json!({
                            "kind": "accepted_swap_gap",
                            "symbol": cfg.symbol,
                            "gap_ms": gap_ms,
                            "previous_signature": previous_watermark.0,
                            "previous_slot": previous_watermark.1,
                            "current_signature": signature,
                            "current_slot": slot,
                            "unix_ms": received
                        })
                        .to_string(),
                    ));
                }
            }

            if event.reference_post().is_none() {
                let mut guard = state.lock().unwrap();
                if let Some(pool) = guard.pools.get_mut(&event.symbol) { pool.quality_quarantined += 1; }
                let _ = log_tx.send(LogRecord::System(json!({"kind":"m2_reference_invalid", "signature":signature, "unix_ms":received}).to_string()));
                continue;
            }

            accepted_signatures.insert(signature.to_owned());
            signature_order.push_back(signature.to_owned());
            if signature_order.len()>32768 {if let Some(old)=signature_order.pop_front(){accepted_signatures.remove(&old);}}
            let decode_us = notification_start.elapsed().as_secs_f64() * 1_000_000.0;

            {
                let mut guard = state.lock().unwrap();
                let learned = update_swap_state(&mut guard, &event, decode_us);
                let _ = log_tx.send(LogRecord::Swap(event.log_json(decode_us)));
                for row in learned {let _=log_tx.send(LogRecord::Learning(serde_json::to_string(&row).expect("label")));}
            }

            queue_depth.fetch_add(1, Ordering::Relaxed);
            match actor_tx.try_send(event) {
                Ok(()) => {}
                Err(_) => {
                    queue_depth.fetch_sub(1, Ordering::Relaxed);
                    let mut guard = state.lock().unwrap();

                    guard.dropped_events += 1;
                }
            }
        }

        let disconnected_ms = now_ms();
        health("DISCONNECTED", generation); generation=generation.saturating_add(1);

        let watermark = {
            let guard = state.lock().unwrap();

            guard
                .pools
                .get(&cfg.symbol)
                .map(|pool| {
                    (
                        pool.last_signature.clone(),
                        pool.last_slot,
                        pool.last_event_unix_ms,
                    )
                })
                .unwrap_or_default()
        };

        let _ = log_tx.send(LogRecord::System(
            json!({
                "kind": "ws_disconnected",
                "symbol": cfg.symbol,
                "unix_ms": disconnected_ms,
                "reason": disconnect_reason,
                "missed_keepalives": missed_keepalives,
                "watermark_signature": watermark.0,
                "watermark_slot": watermark.1,
                "watermark_event_unix_ms": watermark.2
            })
            .to_string(),
        ));

        disconnected_at = Some(disconnected_ms);

        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

fn checkpoint_from_state(state: &EngineState, active_elapsed_ms: u64) -> EngineCheckpoint {
    let mut pools = state
        .pools
        .values()
        .map(|p| PoolCheckpoint {
            symbol: p.cfg.symbol.clone(),

            events: p.events.clone(),

            actor_resolved: p.actor_resolved,

            direct_actor: p.direct_actor,

            swaps_total: p.swaps_total,

            last_meme_reserve: p.last_meme_reserve,

            last_quote_reserve_sol: p.last_quote_reserve_sol,

            multi_raylog_quarantined: p.multi_raylog_quarantined,

            quality_quarantined: p.quality_quarantined,

            last_signature: p.last_signature.clone(),

            last_event_unix_ms: p.last_event_unix_ms,

            last_slot: p.last_slot,
        })
        .collect::<Vec<_>>();
    pools.sort_by(|a,b|a.symbol.cmp(&b.symbol));

    EngineCheckpoint {
        schema: "belial_r5d_m2_checkpoint_v1".to_string(),

        saved_unix_ms: now_ms(),

        active_elapsed_ms,

        latest_slot: state.latest_slot,

        swaps_total: state.swaps_total,

        decode_errors: state.decode_errors,

        actor_attempts: state.actor_attempts,

        actor_rpc_ok: state.actor_rpc_ok,

        direct_actors: state.direct_actors,

        routed_actors: state.routed_actors,

        rpc_errors: state.rpc_errors,

        invalid_signature_quarantined: state.invalid_signature_quarantined,

        quality_quarantined: state
            .pools
            .values()
            .map(|pool| pool.quality_quarantined)
            .sum(),

        learning_records: state.learning_records,

        dropped_events: state.dropped_events,

        decode_samples_us: state.decode_samples_us.clone(),

        wallets: state.wallets.clone(),

        exits: state.exits.clone(),

        history: state.history.clone(),

        pools,
    }
}

fn save_checkpoint(path: &Path, checkpoint: &EngineCheckpoint) -> Result<(), Box<dyn Error>> {
    let bytes = serde_json::to_vec(checkpoint)?;
    let tmp = path.with_extension("json.tmp");
    let mut file = File::create(&tmp)?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    fs::rename(&tmp, path)?;
    if let Some(parent) = path.parent() { File::open(parent)?.sync_all()?; }
    Ok(())
}

fn load_checkpoint(path: &Path) -> Result<Option<EngineCheckpoint>, Box<dyn Error>> {
    if !path.exists() {
        return Ok(None);
    }

    let raw = fs::read_to_string(path)?;

    let checkpoint = serde_json::from_str::<EngineCheckpoint>(&raw)?;

    if checkpoint.schema != "belial_r5d_m2_checkpoint_v1" {
        return Err(format!(
            "checkpoint schema incompatível: {} (esperado belial_r5d_m2_checkpoint_v1)",
            checkpoint.schema
        )
        .into());
    }

    Ok(Some(checkpoint))
}

fn arg_value(args: &[String], key: &str) -> Option<String> {
    args.iter()
        .position(|x| x == key)
        .and_then(|i| args.get(i + 1))
        .cloned()
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    rustls::crypto::ring::default_provider()
        .install_default()
        .map_err(|_| "rustls provider install failed")?;

    let args = env::args().collect::<Vec<_>>();
    if let Some(input)=arg_value(&args,"--fix4-token-manifest") {
        let output=arg_value(&args,"--output").ok_or("--output required")?;
        let document:Value=serde_json::from_str(&std::fs::read_to_string(input)?)?;
        if document["ok"]!=json!(true) || document["token_validation_revision"]!=json!(token_programs::REVISION) {
            return Err("FIX4_TOKEN_MANIFEST_NOT_APPROVED".into());
        }
        let rows=document["pools"].as_array().ok_or("FIX4_POOLS_REQUIRED")?;
        let cfgs:Vec<PoolCfg>=rows.iter().map(|r|serde_json::from_value(r.clone())).collect::<Result<_,_>>()?;
        check_source_scope(&cfgs,a1_scope()).map_err(|e| -> Box<dyn Error> {e.into()})?;
        let mut checked=Vec::new();
        for (i,row) in rows.iter().enumerate() {
            let cfg:PoolCfg=serde_json::from_value(row.clone())?;
            if (!a1_scope() && cfg.symbol!=POOL_SYMBOLS[i]) || cfg.token_contract_revision!=token_programs::REVISION {
                return Err("FIX4_TOKEN_CONTRACT_IDENTITY".into());
            }
            let raw=STANDARD.decode(row["pool_data_b64"].as_str().ok_or("FIX4_POOL_BYTES_REQUIRED")?)?;
            if cfg.owner==RAYDIUM_AMM_V4 {
                if raw.len()!=752 {return Err("FIX4_LEGACY_POOL_LENGTH".into());}
                let c=bs58::encode(&raw[400..432]).into_string();
                let p=bs58::encode(&raw[432..464]).into_string();
                if !((c==cfg.meme_mint && p==WSOL)||(p==cfg.meme_mint && c==WSOL)) {
                    return Err("FIX4_LEGACY_POOL_MINT_PAIR".into());
                }
                let d0=u64_le(&raw,32)?;let d1=u64_le(&raw,40)?;
                if d0>18 || d1>18 {return Err("FIX4_POOL_DECIMALS".into());}
                let meta=PoolMeta{coin_is_meme:c==cfg.meme_mint,coin_mint:c,pc_mint:p,coin_decimals:d0 as u32,pc_decimals:d1 as u32};
                token_programs::verify_cfg(&cfg,&meta)?;
            } else {
                raydium_protocols::meta(&cfg,&cfg.owner,&raw)?;
            }
            checked.push(cfg.symbol);
        }
        let mut out=OpenOptions::new().write(true).create_new(true).open(output)?;
        writeln!(out,"{}",json!({"ok":true,"revision":token_programs::REVISION,"pools":checked,
            "scope":"ARCHIVED_RPC_BYTES_STRUCTURAL_CHECK","scan_started":false,"orders":false}))?;
        out.sync_all()?;
        println!("A1_RUST_TOKEN_CONTRACTS_OK=true;POOLS={};SCAN_STARTED=false",rows.len());
        return Ok(());
    }
    if let Some(input)=arg_value(&args,"--fix2-decode-file") {
        use std::io::BufRead;
        let output=arg_value(&args,"--output").ok_or("--output required")?;
        let reader=std::io::BufReader::new(File::open(input)?);
        let mut out=OpenOptions::new().write(true).create_new(true).open(output)?;
        let mut count=0;
        for line in reader.lines() {
            let raw=line?;if raw.trim().is_empty(){continue;}
            let row:Value=serde_json::from_str(&raw)?;
            let cfg:PoolCfg=serde_json::from_value(row["cfg"].clone())?;
            let data=STANDARD.decode(row["pool_data_b64"].as_str().ok_or("pool_data_b64 missing")?)?;
            let meta=raydium_protocols::meta(&cfg,&cfg.owner,&data)?;
            let event=raydium_protocols::decode(row["event_b64"].as_str().ok_or("event missing")?,&cfg,&meta,
                row["signature"].as_str().ok_or("signature missing")?,row["slot"].as_u64().ok_or("slot missing")?,
                row["received_unix_ns"].as_u64().ok_or("timestamp missing")?/1_000_000,
                row["received_unix_ns"].as_u64().ok_or("timestamp missing")?,row.get("tx"))?;
            writeln!(out,"{}",event.log_json(0.0))?;count+=1;
        }
        out.sync_all()?;
        println!("FIX2_OFFLINE_DECODE_CASES={count};SCAN_STARTED=false");return Ok(());
    }
    if let Some(input) = arg_value(&args,"--m2-reference-check") {
        use std::io::BufRead;
        let output=arg_value(&args,"--output").ok_or("--output required for reference check")?;
        let reader=std::io::BufReader::new(File::open(input)?);
        let mut out=OpenOptions::new().write(true).create_new(true).open(output)?;
        for line in reader.lines() {
            let raw=line?;if raw.trim().is_empty(){continue;}
            let event:SwapEvent=serde_json::from_str(&raw)?;
            let reference=event.reference_post();
            writeln!(out,"{}",json!({"signature":event.signature,"reference":reference}))?;
        }
        out.sync_all()?;
        println!("M2_REFERENCE_CHECK_COMPLETE=true");return Ok(());
    }


    let minutes = arg_value(&args, "--minutes")
        .and_then(|x| x.parse::<u64>().ok())
        .unwrap_or(30);

    let rpc = arg_value(&args, "--rpc")
        .or_else(|| env::var("BELIAL_SOLANA_RPC_URL").ok())
        .unwrap_or_else(|| "https://solana-rpc.publicnode.com".to_string());

    let ws = arg_value(&args, "--ws")
        .or_else(|| env::var("BELIAL_SOLANA_WS_URL").ok())
        .unwrap_or_else(|| "wss://api.mainnet.solana.com".to_string());

    let base = Path::new("/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter");
    let evidence = arg_value(&args, "--evidence")
        .map(PathBuf::from)
        .unwrap_or_else(|| base.join(format!("evidence/m2-direct-paper-{}", now_ms())));
    let state_path = arg_value(&args, "--state")
        .map(PathBuf::from)
        .unwrap_or_else(|| evidence.join("dashboard_state.json"));
    if !evidence.starts_with(base.join("evidence"))
        || !evidence.file_name().and_then(|n|n.to_str()).unwrap_or("").starts_with("m2-")
        || state_path != evidence.join("dashboard_state.json") {
        return Err("M2_REQUIRES_OWN_EVIDENCE_AND_DASHBOARD_PATHS".into());
    }

    fs::create_dir_all(&evidence)?;
    if ["swaps.jsonl","exits.jsonl","checkpoint.json","summary.json"].iter().any(|n| evidence.join(n).exists()) {
        return Err("M2_REQUIRES_NEW_SESSION: existing evidence cannot be overwritten or silently resumed".into());
    }
    let io_failed = Arc::new(AtomicBool::new(false));

    if let Some(parent) = state_path.parent() {
        fs::create_dir_all(parent)?;
    }

    println!("=== BELIAL R5D-M2 QUALIFICATION PAPER ===");

    println!("MINUTES={minutes}");

    println!("RPC={rpc}");

    println!("WS={ws}");

    println!("STATE={}", state_path.display());

    println!("EVIDENCE={}", evidence.display());

    let client = Client::builder()
        .timeout(Duration::from_secs(20))
        .user_agent("Belial-R5D-TailCollector/1.0")
        .build()?;

    println!();
    println!("R5C2 0 -> 10% | pool metadata");

    let mut runtimes = HashMap::<String, PoolRuntime>::new();

    for cfg in pools() {
        let meta = fetch_pool_meta_with_retry(&client, &rpc, &cfg).await?;

        println!(
            "POOL={} coin={} pc={} coin_decimals={} pc_decimals={} coin_is_meme={}",
            cfg.symbol,
            meta.coin_mint,
            meta.pc_mint,
            meta.coin_decimals,
            meta.pc_decimals,
            meta.coin_is_meme
        );

        runtimes.insert(
            cfg.symbol.clone(),
            PoolRuntime {
                cfg,
                meta,

                events: VecDeque::new(),

                actor_resolved: 0,

                direct_actor: 0,

                swaps_total: 0,

                last_meme_reserve: 0.0,

                last_quote_reserve_sol: 0.0,

                multi_raylog_quarantined: 0,

                quality_quarantined: 0,

                last_signature: String::new(),

                last_event_unix_ms: 0,

                last_slot: 0,
            },
        );
    }

    let checkpoint_path = evidence.join("checkpoint.json");

    let checkpoint = load_checkpoint(&checkpoint_path)?;

    let restored_elapsed_ms = checkpoint
        .as_ref()
        .map(|c| c.active_elapsed_ms)
        .unwrap_or(0);

    if let Some(checkpoint) = checkpoint.as_ref() {
        for saved_pool in &checkpoint.pools {
            if let Some(pool) = runtimes.get_mut(&saved_pool.symbol) {
                pool.events = saved_pool.events.clone();

                pool.actor_resolved = saved_pool.actor_resolved;

                pool.direct_actor = saved_pool.direct_actor;

                pool.swaps_total = saved_pool.swaps_total;

                pool.last_meme_reserve = saved_pool.last_meme_reserve;

                pool.last_quote_reserve_sol = saved_pool.last_quote_reserve_sol;

                pool.multi_raylog_quarantined = saved_pool.multi_raylog_quarantined;

                pool.quality_quarantined = saved_pool.quality_quarantined;

                pool.last_signature = saved_pool.last_signature.clone();

                pool.last_event_unix_ms = saved_pool.last_event_unix_ms;

                pool.last_slot = saved_pool.last_slot;
            }
        }
    }

    let started = now_ms().saturating_sub(restored_elapsed_ms);

    let state = Arc::new(Mutex::new(EngineState {
        started_ms: started,

        engine_status: "RUNNING".to_string(),

        latest_slot: checkpoint.as_ref().map(|c| c.latest_slot).unwrap_or(0),

        swaps_total: checkpoint.as_ref().map(|c| c.swaps_total).unwrap_or(0),

        decode_errors: checkpoint.as_ref().map(|c| c.decode_errors).unwrap_or(0),

        actor_attempts: checkpoint.as_ref().map(|c| c.actor_attempts).unwrap_or(0),

        actor_rpc_ok: checkpoint.as_ref().map(|c| c.actor_rpc_ok).unwrap_or(0),

        direct_actors: checkpoint.as_ref().map(|c| c.direct_actors).unwrap_or(0),

        routed_actors: checkpoint.as_ref().map(|c| c.routed_actors).unwrap_or(0),

        rpc_errors: checkpoint.as_ref().map(|c| c.rpc_errors).unwrap_or(0),

        invalid_signature_quarantined: checkpoint
            .as_ref()
            .map(|c| c.invalid_signature_quarantined)
            .unwrap_or(0),

        learning_records: checkpoint.as_ref().map(|c| c.learning_records).unwrap_or(0),

        dropped_events: checkpoint.as_ref().map(|c| c.dropped_events).unwrap_or(0),

        decode_samples_us: checkpoint
            .as_ref()
            .map(|c| c.decode_samples_us.clone())
            .unwrap_or_default(),

        pools: runtimes,

        wallets: checkpoint
            .as_ref()
            .map(|c| c.wallets.clone())
            .unwrap_or_default(),

        exits: checkpoint
            .as_ref()
            .map(|c| c.exits.clone())
            .unwrap_or_default(),

        history: checkpoint
            .as_ref()
            .map(|c| c.history.clone())
            .unwrap_or_default(),
    }));

    let active_elapsed_ms = Arc::new(AtomicU64::new(restored_elapsed_ms));

    println!("CHECKPOINT_PRESENT={}", checkpoint.is_some());

    println!(
        "RESTORED_ACTIVE_SECONDS={:.3}",
        restored_elapsed_ms as f64 / 1000.0
    );

    let queue_depth = Arc::new(AtomicU64::new(0));

    let (actor_tx, actor_rx) = mpsc::channel::<SwapEvent>(ACTOR_QUEUE_CAPACITY);

    let (raw_log_tx, log_rx) = std::sync::mpsc::sync_channel::<LogRecord>(LOG_QUEUE_CAPACITY);
    let log_tx = LogSink {tx: raw_log_tx, failed: io_failed.clone()};
    let evidence_writer = evidence.clone();
    let writer_failed = io_failed.clone();
    let writer = std::thread::spawn(move || {
        let result = writer_task(evidence_writer, log_rx);
        if result.is_err() { writer_failed.store(true,Ordering::Relaxed); }
        result
    });


    let clock_start = Instant::now();
    let clock_handle = {
        let state = state.clone(); let active = active_elapsed_ms.clone(); let log = log_tx.clone();
        tokio::spawn(async move {
            let mut tick = tokio::time::interval(Duration::from_millis(HORIZON_TICK_MS));
            tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                tick.tick().await;
                active.store(clock_start.elapsed().as_millis() as u64,Ordering::Relaxed);
                let mut guard = state.lock().unwrap();
                let now = now_ms();
                for symbol in POOL_SYMBOLS {
                    if !guard.pools.contains_key(symbol) { continue; }
                    let rows=update_exit_returns(&mut guard,symbol,now);
                    guard.learning_records += rows.len() as u64;
                    for row in rows { let _=log.send(LogRecord::Learning(serde_json::to_string(&row).expect("label"))); }
                }
            }
        })
    };
    let checkpoint_stop = Arc::new(AtomicBool::new(false));
    let checkpoint_thread = {
        let state=state.clone(); let active=active_elapsed_ms.clone(); let path=checkpoint_path.clone();
        let stop=checkpoint_stop.clone(); let failed=io_failed.clone();
        std::thread::spawn(move || {
            let mut last=Instant::now();
            while !stop.load(Ordering::Relaxed) {
                std::thread::sleep(Duration::from_millis(100));
                if last.elapsed()<Duration::from_secs(CHECKPOINT_INTERVAL_SECS) { continue; }
                // Only the owned snapshot is copied with the state lock held.
                let snapshot={let g=state.lock().unwrap();checkpoint_from_state(&g,active.load(Ordering::Relaxed))};
                if let Err(e)=save_checkpoint(&path,&snapshot) {
                    failed.store(true,Ordering::Relaxed);eprintln!("M2_CHECKPOINT_FATAL={e}");break;
                }
                last=Instant::now();
            }
        })
    };

    let mut actor_handle = {
        let state = state.clone();

        let depth = queue_depth.clone();

        let client = client.clone();

        let rpc = rpc.clone();

        let log = log_tx.clone();

        tokio::spawn(actor_worker(state, actor_rx, depth, client, rpc, log))
    };

    let mut pool_handles = Vec::new();
    let mut config_checks:HashMap<String,Arc<AtomicU64>>=HashMap::new();
    for symbol in POOL_SYMBOLS {
        let (cfg, meta) = {
            let guard = state.lock().unwrap();

            let Some(pool) = guard.pools.get(symbol) else { continue; };

            (pool.cfg.clone(), pool.meta.clone())
        };

        let config_fresh=if cfg.owner==raydium_protocols::CPMM {
            let fresh=config_checks.entry(cfg.amm_config.clone()).or_insert_with(||{
                let fresh=Arc::new(AtomicU64::new(now_ms()));
                pool_handles.push(tokio::spawn(raydium_protocols::config_guard(client.clone(),rpc.clone(),cfg.clone(),fresh.clone(),state.clone(),log_tx.clone())));
                fresh
            }).clone();Some(fresh)
        }else{None};
        pool_handles.push(tokio::spawn(pool_task(
            cfg,
            meta,
            ws.clone(),
            client.clone(),
            rpc.clone(),
            config_fresh,
            state.clone(),
            actor_tx.clone(),
            queue_depth.clone(),
            log_tx.clone(),
        )));
    }

    let dashboard_handle = {
        let state = state.clone();

        let depth = queue_depth.clone();

        let state_path = state_path.clone();

        tokio::spawn(async move {
            let mut last_history = 0u64;

            loop {
                let now = now_ms();

                let dashboard = {
                    let mut guard = state.lock().unwrap();

                    if now.saturating_sub(last_history) >= 1000 {
                        history_tick(&mut guard, now);

                        last_history = now;
                    }

                    build_dashboard(&guard, depth.load(Ordering::Relaxed), now)
                };

                let _ = atomic_json(&state_path, &dashboard);

                tokio::time::sleep(Duration::from_millis(DASHBOARD_WRITE_INTERVAL_MS)).await;
            }
        })
    };

    println!();
    println!("M2_CAPTURE_STARTED=true");

    let target_active_ms = minutes * 60 * 1000;

    while active_elapsed_ms.load(Ordering::Relaxed) < target_active_ms {
        tokio::time::sleep(Duration::from_secs(1)).await;
        if io_failed.load(Ordering::Relaxed) || evidence.join("REQUEST_STOP").exists() { break; }
        if (active_elapsed_ms.load(Ordering::Relaxed)/1000)%10!=0 {continue;}

        let captured_ms = active_elapsed_ms.load(Ordering::Relaxed);

        let (swaps, wallets, direct, routed, errors, exits) = {
            let guard = state.lock().unwrap();

            (
                guard.swaps_total,
                guard.wallets.len(),
                guard.direct_actors,
                guard.routed_actors,
                guard.rpc_errors,
                guard.exits.len(),
            )
        };

        println!(
            "R5C2_PROGRESS={:.2}% active_seconds={:.1} target_seconds={} swaps={} wallets={} direct={} routed={} exits={} rpc_errors={} queue={}",
            (100.0 * captured_ms as f64 / target_active_ms as f64).min(100.0),
            captured_ms as f64 / 1000.0,
            target_active_ms / 1000,
            swaps,
            wallets,
            direct,
            routed,
            exits,
            errors,
            queue_depth.load(Ordering::Relaxed)
        );
    }

    let capture_target_reached = active_elapsed_ms.load(Ordering::Relaxed) >= target_active_ms;
    for handle in pool_handles { handle.abort(); let _=handle.await; }
    drop(actor_tx);
    let actor_drained = matches!(tokio::time::timeout(Duration::from_secs(25), &mut actor_handle).await, Ok(Ok(())));
    if !actor_handle.is_finished() { actor_handle.abort(); let _=actor_handle.await; }
    clock_handle.abort(); let _=clock_handle.await;
    dashboard_handle.abort(); let _=dashboard_handle.await;
    checkpoint_stop.store(true,Ordering::Relaxed);
    let _=checkpoint_thread.join();
    {
        let mut guard = state.lock().unwrap();
        for symbol in POOL_SYMBOLS {
            if !guard.pools.contains_key(symbol) { continue; }
            let rows=update_exit_returns(&mut guard,symbol,now_ms());
            guard.learning_records+=rows.len() as u64;
            for row in rows {let _=log_tx.send(LogRecord::Learning(serde_json::to_string(&row).expect("final label")));}
        }
        guard.engine_status = if capture_target_reached && actor_drained && !io_failed.load(Ordering::Relaxed) {"DONE"} else {"INCOMPLETE"}.to_string();
    }
    drop(log_tx);
    let writer_stats = writer.join().map_err(|_|"M2_WRITER_PANIC")?.map_err(|e|format!("M2_WRITER_ERROR:{e}"))?;

    tokio::time::sleep(Duration::from_millis(800)).await;

    let final_dashboard = {
        let guard = state.lock().unwrap();

        build_dashboard(&guard, queue_depth.load(Ordering::Relaxed), now_ms())
    };

    atomic_json(&state_path, &final_dashboard)?;

    let final_checkpoint={let guard=state.lock().unwrap();checkpoint_from_state(&guard,active_elapsed_ms.load(Ordering::Relaxed))};
    save_checkpoint(&checkpoint_path,&final_checkpoint)?;

    let summary = {
        let guard = state.lock().unwrap();

        let pool_summary = guard
            .pools
            .values()
            .map(|p| {
                json!({
                    "symbol":
                        p.cfg.symbol,

                    "swaps":
                        p.swaps_total,

                    "actor_resolved":
                        p.actor_resolved,

                    "direct_actor":
                        p.direct_actor,

                    "multi_raylog_quarantined":
                        p.multi_raylog_quarantined,

                    "quality_quarantined":
                        p.quality_quarantined,

                    "last_signature":
                        p.last_signature,

                    "last_slot":
                        p.last_slot,

                    "last_event_unix_ms":
                        p.last_event_unix_ms
                })
            })
            .collect::<Vec<_>>();

        json!({
            "schema":
                "belial_r5d_m2_collector_summary_v1",

            "minutes":
                minutes,

            "active_elapsed_ms":
                active_elapsed_ms.load(
                    Ordering::Relaxed
                ),

            "checkpoint_resume": false,
            "resume_policy": "QUALIFICATION_NEW_SESSION_ONLY_NO_SILENT_REPLAY",
            "capture_target_reached": capture_target_reached,
            "actor_queue_drained": actor_drained,
            "logging_failed": io_failed.load(Ordering::Relaxed),
            "session_complete": capture_target_reached && actor_drained && !io_failed.load(Ordering::Relaxed),
            "checkpoint_interval_secs": CHECKPOINT_INTERVAL_SECS,
            "horizon_tick_ms": HORIZON_TICK_MS,
            "writer": writer_stats,
            "price_basis": "PER_POOL_PROTOCOL_SPECIFIC_NOT_EXECUTION",
            "protocol_revision": "FIX2_AMM_V4_CPMM_CLMM",
            "cpmm_fee_config": "PREFLIGHT_FROZEN_CHECK_EVERY_10S_MAX_AGE_15S_STOP_ON_CHANGE",
            "clmm_reserves": "ACTUAL_CUSTODY_BALANCES_NOT_EXECUTABLE_DEPTH",
            "raw_swap_fields_preserved": true,

            "ws_idle_ping_after_ms":
                WS_IDLE_PING_AFTER_MS,

            "ws_keepalive_max_misses":
                WS_KEEPALIVE_MAX_MISSES,

            "learning_ledger":
                true,

            "swaps_total":
                guard.swaps_total,

            "wallets":
                guard.wallets.len(),

            "actor_attempts":
                guard.actor_attempts,

            "actor_rpc_ok":
                guard.actor_rpc_ok,

            "direct_actors":
                guard.direct_actors,

            "routed_actors":
                guard.routed_actors,

            "rpc_errors":
                guard.rpc_errors,

            "invalid_signature_quarantined":
                guard.invalid_signature_quarantined,

            "quality_quarantined":
                guard.pools
                    .values()
                    .map(|p| p.quality_quarantined)
                    .sum::<u64>(),

            "learning_records":
                guard.learning_records,

            "matured_120s":
                guard.exits
                    .iter()
                    .filter(|e| e.return_120s_bps.is_some())
                    .count(),

            "matured_300s":
                guard.exits
                    .iter()
                    .filter(|e| e.return_300s_bps.is_some())
                    .count(),

            "decode_errors":
                guard.decode_errors,

            "dropped_events":
                guard.dropped_events,

            "exit_candidates":
                guard.exits.len(),

            "pools":
                pool_summary,

            "safety": {
                "read_only": true,
                "orders": false,
                "transactions_submitted": false,
                "bn5_modified": false,
                "p20_modified": false,
                "paper_executable": false
            }
        })
    };

    fs::write(
        evidence.join("summary.json"),
        serde_json::to_vec_pretty(&summary)?,
    )?;

    println!();
    println!("=== BELIAL R5C2 FINAL ===");

    println!("{}", serde_json::to_string_pretty(&summary)?);

    println!("TRANSACTIONS_SUBMITTED=false");

    println!("ORDERS=false");

    println!("BN5_MODIFIED=false");

    println!("P20_MODIFIED=false");

    println!("PAPER_EXECUTABLE=false");

    Ok(())
}

#[cfg(test)]
mod r5c2_tests {
    use super::*;

    fn exit_fixture() -> ExitRecord {
        ExitRecord {
            event_id: "sig|wallet|1000".to_string(),
            symbol: "FARTCOIN".to_string(),
            wallet: "wallet".to_string(),
            event_unix_ms: 1_000,
            signal_available_unix_ms: 0,
            actor_resolution_latency_ms: 0,
            entry_price: 1.0,
            evaluated_horizons: Vec::new(),
            entry_price_basis: "POST_RESERVES_DIAGNOSTIC_NOT_EXECUTION".to_string(),
            observed_entry_price: Some(1.0),
            inventory_before: 100.0,
            sell_qty: 10.0,
            inventory_exit_pct: 10.0,
            cluster_size: 2,
            sell_quote_sol: 5.0,
            sell_pool_reserve_pct: 0.10,
            observed_inventory_pool_pct: 0.20,
            wallet_sell_flow_share_1s_pct: 20.0,
            wallet_sell_flow_share_5s_pct: 20.0,
            wallet_sell_flow_share_30s_pct: 20.0,
            cluster_unique_wallets_10s: 2,
            cluster_sell_quote_sol_10s: 8.0,
            pre_return_30s_bps: 15.0,
            pre_sell_pressure_30s_pct: -30.0,
            absorption_score: 0.0,
            sell_efficiency_5s_score: 0.0,
            regime: "BUY_DOMINANT".to_string(),
            leader_quality_120s_at_entry: 50.0,
            leader_samples_120s_at_entry: 8,
            tail_candidate_score: 0.0,
            impact_score: 70.0,
            return_500ms_bps: None,
            return_1s_bps: None,
            return_2s_bps: None,
            return_3s_bps: None,
            return_5s_bps: None,
            return_10s_bps: None,
            return_30s_bps: None,
            return_60s_bps: None,
            return_120s_bps: None,
            return_300s_bps: None,
        }
    }

    #[test]
    fn t3_universe_twelve_unique_fixtures_no_network() {
        let cfgs=pools();assert_eq!(cfgs.len(),12);
        assert_eq!(cfgs.iter().map(|c|c.pool.clone()).collect::<HashSet<_>>().len(),12);
        assert_eq!(cfgs.iter().map(|c|c.meme_mint.clone()).collect::<HashSet<_>>().len(),12);
    }

    #[test]
    fn placeholder_signature_is_quarantined_before_rpc() {
        assert!(placeholder_signature(
            "1111111111111111111111111111111111111111111111111111111111111111"
        ));
        assert!(!placeholder_signature(
            "5hQxRealisticSignatureNotAllOnesxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        ));
    }

    #[test]
    fn future_horizon_never_matures_early() {
        let mut exit = exit_fixture();
        let mut points = VecDeque::new();

        points.push_back(PoolPoint {
            ts_ms: 1_000,
            price: 1.0,
            side: Side::Buy,
            meme_qty: 1.0,
        });

        points.push_back(PoolPoint {
            ts_ms: 6_000,
            price: 0.99,
            side: Side::Sell,
            meme_qty: 1.0,
        });

        let early = fill_exit(&mut exit, &points, 5_999);

        assert!(exit.return_5s_bps.is_none());
        assert!(!early.iter().any(|x| x.horizon_ms == 5_000));

        let mature = fill_exit(&mut exit, &points, 6_000);

        let r5 = exit.return_5s_bps.expect("5s mature");
        assert!((r5 + 100.0).abs() < 1e-9);
        assert!(mature.iter().any(|x| x.horizon_ms == 5_000));
        assert!(exit.sell_efficiency_5s_score > 0.0);
    }

    #[test]
    fn stale_asof_price_cannot_complete_long_horizon() {
        let mut exit = exit_fixture();
        let mut points = VecDeque::new();

        points.push_back(PoolPoint {
            ts_ms: 1_000,
            price: 1.0,
            side: Side::Buy,
            meme_qty: 1.0,
        });

        let learned = fill_exit(&mut exit, &points, 121_000);

        assert!(exit.return_120s_bps.is_none());
        assert!(learned.iter().any(|x| x.horizon_ms == 120_000 && x.return_bps.is_none() && x.status == "MARK_MISSING_OR_STALE"));
    }
    #[test] fn missing_horizon_emits_null_once() {
        let mut e=exit_fixture(); let p=VecDeque::new();
        let rows=fill_exit(&mut e,&p,6000);
        assert_eq!(rows.len(),5);
        assert!(rows.iter().all(|r|r.return_bps.is_none()));
        assert!(fill_exit(&mut e,&p,6000).is_empty());
    }
    #[test] fn delayed_actor_no_future_or_backdate() {
        let mut e=exit_fixture();e.signal_available_unix_ms=8000;
        let p=VecDeque::from([PoolPoint {ts_ms:5900,price:0.99,side:Side::Sell,meme_qty:1.0}]);
        assert!(fill_exit(&mut e,&p,7000).is_empty());
        let rows=fill_exit(&mut e,&p,8100);
        let row=rows.iter().find(|r|r.horizon_ms==5000).unwrap();
        assert_eq!(row.matured_unix_ms,8100);assert_eq!(row.horizon_due_unix_ms,6000);
        assert_eq!(row.publication_delay_ms,100);assert_eq!(row.signal_age_ms,7100);
        assert_eq!(row.asof_age_ms,Some(100));
    }
    #[test] fn future_mark_not_selected() {
        let mut e=exit_fixture();let p=VecDeque::from([PoolPoint {ts_ms:6100,price:0.8,side:Side::Sell,meme_qty:1.0}]);
        let rows=fill_exit(&mut e,&p,6200);
        assert!(rows.iter().find(|r|r.horizon_ms==5000).unwrap().return_bps.is_none());
    }
    #[test] fn terminal_null_never_rewritten_by_late_data() {
        let mut e=exit_fixture();fill_exit(&mut e,&VecDeque::new(),6000);
        let p=VecDeque::from([PoolPoint {ts_ms:5900,price:0.8,side:Side::Sell,meme_qty:1.0}]);
        assert!(fill_exit(&mut e,&p,6200).is_empty());assert!(e.return_5s_bps.is_none());
    }
    #[test] fn bookkeeping_roundtrips_checkpoint() {
        let mut e=exit_fixture();fill_exit(&mut e,&VecDeque::new(),6000);
        let restored:ExitRecord=serde_json::from_slice(&serde_json::to_vec(&e).unwrap()).unwrap();
        assert_eq!(restored.evaluated_horizons,e.evaluated_horizons);
        assert_eq!(restored.entry_price_basis,e.entry_price_basis);
    }
    #[test] fn log_queue_full_is_fatal_not_silent() {
        let (tx,_rx)=std::sync::mpsc::sync_channel(1);let failed=Arc::new(AtomicBool::new(false));
        let sink=LogSink {tx,failed:failed.clone()};sink.send(LogRecord::Swap("a".into())).unwrap();
        assert!(sink.send(LogRecord::Swap("b".into())).is_err());assert!(failed.load(Ordering::Relaxed));
    }
    #[test] fn closed_log_queue_is_fatal() {
        let (tx,rx)=std::sync::mpsc::sync_channel(1);drop(rx);let f=Arc::new(AtomicBool::new(false));
        let sink=LogSink {tx,failed:f.clone()};assert!(sink.send(LogRecord::Swap("x".into())).is_err());assert!(f.load(Ordering::Relaxed));
    }
    #[test] fn log_writer_drains_and_syncs_once_per_dirty_file() {
        let dir=std::env::temp_dir().join(format!("belial-m2-writer-{}-{}",std::process::id(),SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()));
        fs::create_dir(&dir).unwrap();let (tx,rx)=std::sync::mpsc::sync_channel(128);
        for _ in 0..100 {tx.send(LogRecord::Swap("{}".into())).unwrap();}drop(tx);
        let result=writer_task(dir.clone(),rx).unwrap();assert_eq!(result.rows,100);assert_eq!(result.bytes,fs::metadata(dir.join("swaps.jsonl")).unwrap().len());assert_eq!(result.sync_calls,1);
        assert_eq!(fs::read_to_string(dir.join("swaps.jsonl")).unwrap().lines().count(),100);fs::remove_dir_all(dir).unwrap();
    }
    #[test] fn writer_refuses_existing_history() {
        let dir=std::env::temp_dir().join(format!("belial-m2-existing-{}-{}",std::process::id(),SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()));
        fs::create_dir(&dir).unwrap();fs::write(dir.join("swaps.jsonl"),b"preserve\n").unwrap();
        let (tx,rx)=std::sync::mpsc::sync_channel(1);drop(tx);assert!(writer_task(dir.clone(),rx).is_err());
        assert_eq!(fs::read(dir.join("swaps.jsonl")).unwrap(),b"preserve\n");fs::remove_dir_all(dir).unwrap();
    }
    #[test] fn invalid_return_stays_none() {assert!(return_bps(f64::NAN,1.0).is_none());assert!(return_bps(1.0,f64::INFINITY).is_none());}

}

#[cfg(test)]mod a1_scope_tests {
 use super::*;
 fn cfg(i:usize)->PoolCfg {PoolCfg{symbol:POOL_SYMBOLS[i].into(),meme_mint:A1_MINTS[i].into(),..Default::default()}}
 #[test]fn a1_one_sensor_not_twelve(){assert!(check_source_scope(&[cfg(0)],true).is_ok());}
 #[test]fn a1_empty_scope_refused(){assert!(check_source_scope(&[],true).is_err());}
 #[test]fn a1_two_sensors_in_isolated_process_refused(){assert!(check_source_scope(&[cfg(0),cfg(1)],true).is_err());}
 #[test]fn full_scope_still_requires_twelve(){assert!(check_source_scope(&[cfg(0)],false).is_err());}
 #[test]fn every_canonical_asset_individually_supported_by_scope(){for i in 0..12 {assert!(check_source_scope(&[cfg(i)],true).is_ok());}}
 #[test]fn scope_is_not_mint_bypass(){let mut c=cfg(8);c.meme_mint=A1_MINTS[0].into();assert!(check_source_scope(&[c],true).is_err());}
 #[test]fn unknown_symbol_rejected(){let mut c=cfg(0);c.symbol="SOL".into();assert!(check_source_scope(&[c],true).is_err());}
 #[test]fn original_canonical_order_passes(){let all=(0..12).map(cfg).collect::<Vec<_>>();assert!(check_source_scope(&all,false).is_ok());}
}
