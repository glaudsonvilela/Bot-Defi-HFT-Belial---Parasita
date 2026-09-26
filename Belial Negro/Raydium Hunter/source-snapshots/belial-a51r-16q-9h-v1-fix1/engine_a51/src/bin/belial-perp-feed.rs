//! Read-only multiplexed WebSocket transport. No transaction, order, signer or account API.
//! Emits every data frame; bounded channel applies backpressure, never drops silently.
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use std::{
    io::{self, BufRead, Write},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::sync::{broadcast, mpsc};
use tokio_tungstenite::{connect_async, tungstenite::Message};
fn ns() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}
fn subscriptions(venue: &str, markets: &[Value]) -> Vec<Value> {
    let mut subs = Vec::new();
    for m in markets.iter().filter(|m| m["venue"] == venue) {
        let Some(market) = m["market"].as_str() else {
            continue;
        };
        if venue == "hyperliquid" {
            // fast=true: exchange's fast five-level stream. Insufficient depth => reject fill.
            subs.push(json!({"method":"subscribe","subscription":{"type":"l2Book","coin":market,"fast":true}}));
            subs.push(json!({"method":"subscribe","subscription":{"type":"activeAssetCtx","coin":market}}));
        } else {
            for kind in ["order_book", "ticker", "market_stats"] {
                subs.push(json!({"type":"subscribe","channel":format!("{kind}/{market}")}));
            }
        }
    }
    subs
}
async fn worker(
    venue: &'static str,
    markets: Vec<Value>,
    tx: mpsc::Sender<Value>,
    mut control: broadcast::Receiver<String>,
) {
    let url = if venue == "hyperliquid" {
        "wss://api.hyperliquid.xyz/ws"
    } else {
        "wss://mainnet.zklighter.elliot.ai/stream"
    };
    let subs = subscriptions(venue, &markets);
    if subs.is_empty() {
        return;
    }
    let mut generation = 0u64;
    let mut backoff = 1u64;
    loop {
        generation += 1;
        let result = tokio::time::timeout(Duration::from_secs(15), connect_async(url)).await;
        if let Ok(Err(tokio_tungstenite::tungstenite::Error::Http(response))) = &result {
            let status = response.status().as_u16();
            if status == 401 || status == 403 {
                let _=tx.send(json!({"venue":venue,"generation":generation,"received_ns":ns(),"transport":"ACCESS_DENIED","http_status":status})).await;
                return;
            }
        }
        if let Ok(Ok((mut socket, _))) = result {
            backoff = 1;
            if tx.send(json!({"venue":venue,"generation":generation,"received_ns":ns(),"transport":"CONNECTED"})).await.is_err(){return;}
            let mut subscribed = true;
            for sub in &subs {
                if socket
                    .send(Message::Text(sub.to_string().into()))
                    .await
                    .is_err()
                {
                    subscribed = false;
                    break;
                }
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
            if subscribed {
                let mut ping = tokio::time::interval(Duration::from_secs(20));
                let mut last = std::time::Instant::now();
                loop {
                    tokio::select! {
                     msg=socket.next()=> {
                      match msg {
                       Some(Ok(Message::Text(s)))=>{
                        let at=ns();last=std::time::Instant::now();
                        match serde_json::from_str::<Value>(&s) {
                         Ok(body)=>{
                          if body["type"]=="ping" {let _=socket.send(Message::Text(json!({"type":"pong"}).to_string().into())).await;}
                          if tx.send(json!({"venue":venue,"generation":generation,"received_ns":at,"data":body})).await.is_err(){return;}
                         },
                         Err(e)=>{let _=tx.send(json!({"venue":venue,"generation":generation,"received_ns":at,"transport":"INVALID_JSON","error":e.to_string()})).await;break;}
                        }
                       },
                       Some(Ok(Message::Ping(data)))=>{last=std::time::Instant::now();if socket.send(Message::Pong(data)).await.is_err(){break;}},
                       Some(Ok(Message::Pong(_)))=>{last=std::time::Instant::now();},
                       Some(Ok(Message::Close(_)))|None|Some(Err(_))=>break,
                       _=>{}
                      }
                     },
                     _=ping.tick()=> {
                      if last.elapsed()>Duration::from_secs(60){break;}
                      let p=if venue=="hyperliquid" {json!({"method":"ping"})} else {json!({"type":"ping"})};
                      if socket.send(Message::Text(p.to_string().into())).await.is_err(){break;}
                     },
                     cmd=control.recv()=>{
                      if let Ok(cmd)=cmd {if cmd=="STOP" {let _=socket.close(None).await;return;} if cmd==venue {let _=socket.close(None).await;break;}}
                     }
                    }
                }
            }
        }
        if tx.send(json!({"venue":venue,"generation":generation,"received_ns":ns(),"transport":"DISCONNECTED","retry_seconds":backoff})).await.is_err(){return;}
        tokio::select! {
         _=tokio::time::sleep(Duration::from_secs(backoff))=>{},
         cmd=control.recv()=>{if let Ok(cmd)=cmd {if cmd=="STOP" {return;}}}
        }
        backoff = (backoff * 2).min(15);
    }
}
#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let path = std::env::args()
        .nth(1)
        .ok_or("usage: belial-perp-feed MARKETS.json")?;
    let root: Value = serde_json::from_str(&std::fs::read_to_string(path)?)?;
    if root["ok"] != true {
        return Err("metadata not approved".into());
    }
    let markets = root["markets"].as_array().ok_or("markets absent")?.clone();
    let (tx, mut rx) = mpsc::channel::<Value>(8192);
    let (control, _) = broadcast::channel::<String>(16);
    let c = control.clone();
    std::thread::spawn(move || {
        for line in io::stdin().lock().lines() {
            let Ok(line) = line else {
                break;
            };
            if let Ok(v) = serde_json::from_str::<Value>(&line) {
                if v["stop"] == true {
                    let _ = c.send("STOP".to_string());
                    return;
                }
                if let Some(s) = v["reconnect"].as_str() {
                    if ["hyperliquid", "lighter"].contains(&s) {
                        let _ = c.send(s.to_string());
                    }
                }
            }
        }
        let _ = c.send("STOP".to_string());
    });
    let writer = std::thread::spawn(move || -> io::Result<()> {
        let stdout = io::stdout();
        let mut out = io::BufWriter::new(stdout.lock());
        while let Some(mut row) = rx.blocking_recv() {
            row["writer_ns"] = json!(ns());
            serde_json::to_writer(&mut out, &row).map_err(io::Error::other)?;
            out.write_all(b"\n")?;
            out.flush()?;
        }
        Ok(())
    });
    let a = tokio::spawn(worker(
        "hyperliquid",
        markets.clone(),
        tx.clone(),
        control.subscribe(),
    ));
    let b = tokio::spawn(worker("lighter", markets, tx.clone(), control.subscribe()));
    drop(tx);
    let _ = tokio::join!(a, b);
    writer.join().map_err(|_| "writer panic")??;
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_read_subscriptions() {
        for v in ["hyperliquid", "lighter"] {
            let s = subscriptions(v, &[json!({"venue":v,"market":"X"})]);
            assert!(!s.is_empty());
            for x in s {
                let t = x.to_string();
                assert!(!t.contains("sendtx"));
                assert!(!t.contains("orderUpdates"));
                assert!(!t.contains("user"));
            }
        }
    }
    #[test]
    fn hl_fast_and_ctx() {
        let s = subscriptions(
            "hyperliquid",
            &[json!({"venue":"hyperliquid","market":"WIF"})],
        );
        assert_eq!(s.len(), 2);
        assert_eq!(s[0]["subscription"]["fast"], true);
    }
    #[test]
    fn venue_filter() {
        assert!(
            subscriptions("lighter", &[json!({"venue":"hyperliquid","market":"WIF"})]).is_empty()
        );
    }
}
