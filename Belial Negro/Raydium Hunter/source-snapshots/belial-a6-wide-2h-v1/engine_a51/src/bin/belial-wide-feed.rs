//! Public market data only. No keys, accounts or order endpoints.
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use std::{collections::{BTreeSet, HashMap}, io::{self, Write}, path::PathBuf, sync::{Arc, atomic::{AtomicU64, Ordering}}, time::{Duration, SystemTime, UNIX_EPOCH}};
use tokio::sync::mpsc;
use tokio_tungstenite::{connect_async, tungstenite::Message};
fn now() -> u64 {SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64}
fn allowed(s:&str)->bool {s.len()<=40 && !s.is_empty() && s.bytes().all(|b|b.is_ascii_alphanumeric())}
#[derive(Clone)]
struct Output {tx:mpsc::Sender<Value>, loss:Arc<AtomicU64>}
impl Output {
 fn send(&self, mut v:Value) {v["received_ms"]=json!(now());v["loss_epoch"]=json!(self.loss.load(Ordering::Relaxed));v["orders"]=json!(false);if self.tx.try_send(v).is_err(){self.loss.fetch_add(1,Ordering::Relaxed);}}
}
fn selection(path:&PathBuf)->BTreeSet<String>{
 let v:Value=std::fs::read(path).ok().and_then(|b|serde_json::from_slice(&b).ok()).unwrap_or(json!({}));
 v["symbols"].as_array().into_iter().flatten().filter_map(Value::as_str).filter(|s|allowed(s)).take(64).map(str::to_lowercase).collect()
}
fn streams(path:&PathBuf,kind:&str)->BTreeSet<String>{
 if kind=="wide" {return ["!miniTicker@arr".into(),"!markPrice@arr".into()].into_iter().collect()}
 let suffix=if kind=="depth"{"@depth20@100ms"}else{"@aggTrade"};
 selection(path).iter().map(|s|format!("{s}{suffix}")).collect()
}
#[derive(Default)]
struct Flow {buy:f64,sell:f64,n:u64,start:u64,end:u64,last_id:u64}
async fn feed(kind:&'static str,path:PathBuf,out:Output){
 let zone=if kind=="depth"{"public"}else{"market"};let url=format!("wss://fstream.binance.com/{zone}/stream");
 let mut generation=0u64;let mut backoff=1u64;
 loop {
  generation+=1;out.send(json!({"kind":"transport","channel":kind,"state":"CONNECTING","generation":generation}));
  let conn=tokio::time::timeout(Duration::from_secs(12),connect_async(url.as_str())).await;
  let (mut ws,_) = match conn {
   Ok(Ok(v))=>v,
   Ok(Err(tokio_tungstenite::tungstenite::Error::Http(r))) if [401,403,451].contains(&r.status().as_u16())=>{out.send(json!({"kind":"transport","channel":kind,"state":"ACCESS_DENIED","http":r.status().as_u16()}));return;},
   _=>{out.send(json!({"kind":"transport","channel":kind,"state":"RECONNECTING","generation":generation}));tokio::time::sleep(Duration::from_secs(backoff)).await;backoff=(backoff*2).min(60);continue;}
  };
  out.send(json!({"kind":"transport","channel":kind,"state":"CONNECTED","generation":generation}));
  let mut subscribed=BTreeSet::<String>::new();let mut flows=HashMap::<String,Flow>::new();let mut last_ids=HashMap::<String,u64>::new();
  let mut timer=tokio::time::interval(Duration::from_secs(1));timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
  let mut tick=0;let mut id=0u64;let mut last_message=now();let connected_at=now();
  loop {
   tokio::select! {
    _=timer.tick()=>{
     tick+=1;
     if now().saturating_sub(last_message)>60_000 {break}
     if tick%5==1 {
      let wanted=streams(&path,kind);
      let mut send_failed=false;
      for (method,params) in [("UNSUBSCRIBE",subscribed.difference(&wanted).cloned().collect::<Vec<_>>()),("SUBSCRIBE",wanted.difference(&subscribed).cloned().collect::<Vec<_>>())] {
       if !params.is_empty(){id+=1;if ws.send(Message::Text(json!({"method":method,"params":params,"id":id.to_string()}).to_string().into())).await.is_err(){send_failed=true;break}}
      }
      if send_failed {break}
      subscribed=wanted;
      let names=selection(&path);last_ids.retain(|s,_|names.contains(&s.to_lowercase()));
      out.send(json!({"kind":"subscription","channel":kind,"generation":generation,"requested":subscribed.len()}));
     }
     for (s,f) in flows.drain(){out.send(json!({"kind":"flow","channel":kind,"symbol":s,"generation":generation,"buy":f.buy,"sell":f.sell,"trades":f.n,"first_ms":f.start,"event_ms":f.end}));}
    },
    msg=ws.next()=>{
     let text=match msg {Some(Ok(Message::Text(t)))=>t,Some(Ok(Message::Ping(p)))=>{if ws.send(Message::Pong(p)).await.is_err(){break}continue},Some(Ok(Message::Close(_)))|Some(Err(_))|None=>break,_=>continue};
     last_message=now();if now().saturating_sub(connected_at)>30_000{backoff=1;}
     if text.len()>8*1024*1024 {out.loss.fetch_add(1,Ordering::Relaxed);continue;}
     let v:Value=match serde_json::from_str(&text){Ok(v)=>v,Err(_)=>{out.loss.fetch_add(1,Ordering::Relaxed);continue}};
     if v.get("code").is_some(){out.send(json!({"kind":"transport","channel":kind,"state":"SUBSCRIPTION_ERROR","code":v["code"]}));break}
     if v.get("id").is_some(){out.send(json!({"kind":"ack","channel":kind,"id":v["id"],"ok":v["result"].is_null()}));continue}
     let d=v.get("data").unwrap_or(&v);
     if kind=="flow" {
      if d["e"]!="aggTrade" || d["st"].as_u64()==Some(2){continue}
      let Some(s)=d["s"].as_str().filter(|s|allowed(s)) else {continue};
      let event=d["T"].as_u64().unwrap_or(0);let ident=d["a"].as_u64().unwrap_or(0);
      if event>now()+500 || now().saturating_sub(event)>2000 || ident<=*last_ids.get(s).unwrap_or(&0){continue}
      let p=d["p"].as_str().and_then(|x|x.parse::<f64>().ok()).unwrap_or(0.);let q=d["q"].as_str().and_then(|x|x.parse::<f64>().ok()).unwrap_or(0.);
      if p<=0. || q<=0. || !(p*q).is_finite(){continue}
      last_ids.insert(s.into(),ident);
      let f=flows.entry(s.into()).or_default();if f.n==0{f.start=event}f.end=event;f.last_id=ident;f.n+=1;
      if d["m"]==true {f.sell+=p*q}else{f.buy+=p*q}
     } else {out.send(json!({"kind":kind,"channel":kind,"generation":generation,"data":d}));}
    }
   }
  }
  out.send(json!({"kind":"transport","channel":kind,"state":"DISCONNECTED","generation":generation}));
  tokio::time::sleep(Duration::from_secs(backoff)).await;backoff=(backoff*2).min(60);
 }
}
async fn metadata(out:Output){
 let client=reqwest::Client::builder().timeout(Duration::from_secs(12)).build().expect("http client");
 loop {
  match client.get("https://fapi.binance.com/fapi/v1/exchangeInfo").send().await {
   Ok(r) if r.status().is_success()=>{match r.json::<Value>().await {Ok(v)=>out.send(json!({"kind":"metadata","data":v})),Err(_)=>out.send(json!({"kind":"metadata_error","reason":"INVALID_JSON"}))}},
   Ok(r)=>{let code=r.status().as_u16();out.send(json!({"kind":"metadata_error","http":code}));if [401,403,451].contains(&code){return}},
   Err(_)=>out.send(json!({"kind":"metadata_error","reason":"TRANSPORT"}))
  }
  tokio::time::sleep(Duration::from_secs(600)).await;
 }
}
#[tokio::main(flavor="multi_thread",worker_threads=3)]
async fn main(){
 let _=rustls::crypto::ring::default_provider().install_default();
 let Some(path)=std::env::args().nth(1) else {eprintln!("usage: belial-wide-feed selection.json");std::process::exit(2)};
 let(tx,mut rx)=mpsc::channel::<Value>(2048);let out=Output{tx,loss:Arc::new(AtomicU64::new(0))};
 std::thread::spawn(move||{let stdout=io::stdout();let mut w=io::BufWriter::new(stdout.lock());while let Some(v)=rx.blocking_recv(){if writeln!(w,"{v}").and_then(|_|w.flush()).is_err(){std::process::exit(0)}}});
 let p=PathBuf::from(path);
 tokio::join!(feed("wide",p.clone(),out.clone()),feed("depth",p.clone(),out.clone()),feed("flow",p,out.clone()),metadata(out));
}
#[cfg(test)]mod tests {use super::*;#[test]fn market_validation(){assert!(allowed("1000BONKUSDT"));assert!(!allowed("x@depth/secret"));assert!(!allowed(""));}#[test]fn wide_routes(){let s=streams(&PathBuf::new(),"wide");assert_eq!(s.len(),2);assert!(s.contains("!miniTicker@arr"));}#[test]fn missing_selection(){assert!(selection(&PathBuf::from("/nonexistent/belial-selection")).is_empty());}}
