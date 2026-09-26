//! A1 public Solana raw-log recorder. No decoder and no transaction submission.
//! Raw messages never count as validated prices, wallet flow or PAPER signals.
use std::{io::{self,BufRead,Write},time::{Duration,SystemTime,UNIX_EPOCH},collections::HashMap};
use futures_util::{SinkExt,StreamExt};
use serde_json::{json,Value};
use tokio::sync::{watch,mpsc};
use tokio_tungstenite::{connect_async,tungstenite::{Message,Error}};
fn ns()->u64 {SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos() as u64}
fn subscriptions(config:&Value)->Result<Vec<(String,Value)>,String> {
 if config["ok"]!=true || config["orders"]!=false || config["read_only"]!=true {return Err("RAW_CONFIG_NOT_READ_ONLY".into());}
 let rows=config["sources"].as_array().ok_or("RAW_SOURCES_REQUIRED")?;
 if rows.len()>24 {return Err("RAW_MAX_TWENTY_FOUR".into());}
 let mut seen=std::collections::HashSet::new();let mut out=Vec::new();
 for (i,row) in rows.iter().enumerate() {
  let symbol=row["symbol"].as_str().ok_or("RAW_SYMBOL_REQUIRED")?;
  let pool=row["pool"].as_str().ok_or("RAW_POOL_REQUIRED")?;
  if bs58::decode(pool).into_vec().map_err(|_|"RAW_POOL_INVALID")?.len()!=32 || !seen.insert(pool) {return Err("RAW_POOL_INVALID_OR_DUPLICATE".into());}
  out.push((symbol.to_string(),json!({"jsonrpc":"2.0","id":i+1,"method":"logsSubscribe","params":[{"mentions":[pool]},{"commitment":"confirmed"}]})));
 }
 Ok(out)
}
#[tokio::main(flavor="multi_thread",worker_threads=1)]
async fn main()->Result<(),Box<dyn std::error::Error>> {
 let _=rustls::crypto::ring::default_provider().install_default();
 let args:Vec<String>=std::env::args().collect();let path=args.get(1).ok_or("usage: belial-spot-raw CONFIG.json")?;
 let config:Value=serde_json::from_str(&std::fs::read_to_string(path)?)?;
 let subs=subscriptions(&config)?;let url=config["ws"].as_str().ok_or("RAW_WS_REQUIRED")?;
 if !url.starts_with("wss://") {return Err("RAW_REQUIRES_TLS".into());}
 let(tx,mut rx)=mpsc::channel::<Value>(2048);let(stop_tx,mut stop)=watch::channel(false);
 std::thread::spawn(move||{for line in io::stdin().lock().lines(){if let Ok(line)=line {if line.contains("stop"){break;}}else{break;}}let _=stop_tx.send(true);});
 let writer=std::thread::spawn(move||->io::Result<()>{let stdout=io::stdout(); let mut out=io::BufWriter::new(stdout.lock());let mut seq=0u64;
  while let Some(mut row)=rx.blocking_recv(){seq+=1;row["source_sequence"]=json!(seq);row["writer_ns"]=json!(ns());
   serde_json::to_writer(&mut out,&row).map_err(io::Error::other)?;out.write_all(b"\n")?;out.flush()?;}Ok(())});
 let mut generation=0u64;let mut backoff=1u64;
 while !*stop.borrow() && !subs.is_empty() {
  generation+=1;let mut denied=false;
  match tokio::time::timeout(Duration::from_secs(15),connect_async(url)).await {
   Ok(Ok((mut socket,_)))=>{
    backoff=1;let _=tx.send(json!({"transport":"CONNECTED","generation":generation,"received_ns":ns(),"raw_only":true})).await;
    let mut identities:HashMap<u64,String>=HashMap::new();
    let mut sent=true;
    for (_,request) in &subs {if socket.send(Message::Text(request.to_string().into())).await.is_err(){sent=false;break;}tokio::time::sleep(Duration::from_millis(25)).await;}
    let mut tick=tokio::time::interval(Duration::from_secs(20));let mut last=std::time::Instant::now();
    while sent && !*stop.borrow() {
     tokio::select! {
      msg=socket.next()=>{
       match msg {
        Some(Ok(Message::Text(text)))=>{
         last=std::time::Instant::now();let at=ns();
         match serde_json::from_str::<Value>(&text) {
          Ok(data)=>{
           if let (Some(id),Some(sub))=(data["id"].as_u64(),data["result"].as_u64()) {
            if id>0 && (id as usize)<=subs.len(){identities.insert(sub,subs[id as usize-1].0.clone());}
           }
           let symbol=data["params"]["subscription"].as_u64().and_then(|s|identities.get(&s)).cloned();
           if tx.send(json!({"generation":generation,"received_ns":at,"symbol":symbol,"data":data,"raw_only":true,"price_validated":false,"signal":false})).await.is_err(){sent=false;}
          },
          Err(_)=>{let _=tx.send(json!({"transport":"INVALID_JSON","generation":generation,"received_ns":at,"raw_only":true})).await;break;}
         }
        },
        Some(Ok(Message::Ping(data)))=>{last=std::time::Instant::now();if socket.send(Message::Pong(data)).await.is_err(){break;}},
        Some(Ok(Message::Pong(_)))=>{last=std::time::Instant::now();},
        Some(Ok(Message::Close(_)))|None|Some(Err(_))=>break,
        _=>{}
       }
      },
      _=tick.tick()=>{if last.elapsed()>Duration::from_secs(60){break;}if socket.send(Message::Ping(Vec::new().into())).await.is_err(){break;}},
      _=stop.changed()=>{let _=socket.close(None).await;break;}
     }
    }
   },
   Ok(Err(Error::Http(response)))=>{denied=matches!(response.status().as_u16(),401|403);},
   _=>{}
  }
  if tx.send(json!({"transport":if denied{"ACCESS_DENIED"}else{"DISCONNECTED"},"generation":generation,"received_ns":ns(),"raw_only":true})).await.is_err(){break;}
  if denied{break;}
  tokio::select!{_=tokio::time::sleep(Duration::from_secs(backoff))=>{},_=stop.changed()=>{}}
  backoff=(backoff*2).min(30);
 }
 drop(tx);writer.join().map_err(|_|"RAW_WRITER_PANIC")??;Ok(())
}
#[cfg(test)]mod tests {
 use super::*;
 fn config()->Value {json!({"ok":true,"orders":false,"read_only":true,"sources":[{"symbol":"PUMP","pool":bs58::encode([8u8;32]).into_string()}]})}
 #[test]fn only_public_logs(){let s=subscriptions(&config()).unwrap();assert_eq!(s[0].1["method"],"logsSubscribe");assert_eq!(s[0].1["params"][1]["commitment"],"confirmed");}
 #[test]fn no_orders_flag(){let mut c=config();c["orders"]=json!(true);assert!(subscriptions(&c).is_err());}
 #[test]fn invalid_pool(){let mut c=config();c["sources"][0]["pool"]=json!("invalid");assert!(subscriptions(&c).is_err());}
 #[test]fn empty_sources_no_fake_stream(){let mut c=config();c["sources"]=json!([]);assert!(subscriptions(&c).unwrap().is_empty());}
 #[test]fn duplicate_refused(){let mut c=config();let row=c["sources"][0].clone();c["sources"].as_array_mut().unwrap().push(row);assert!(subscriptions(&c).is_err());}
}
