//! Pure price/calendar contracts, no RPC, filesystem, framework or trading API.
pub const HORIZONS: [u64; 10] = [500,1000,2000,3000,5000,10000,30000,60000,120000,300000];
/// ray_log reserves are pre-swap. Preserve observed deltas, do not deduct fees again.
pub fn post_reference(x:f64,y:f64,qty:f64,quote:f64,buy:bool)->Option<f64> {
    if [x,y,qty,quote].iter().any(|v| !v.is_finite() || *v<=0.0) {return None;}
    let (xp,yp)=if buy {(x-qty,y+quote)} else {(x+qty,y-quote)};
    if xp<=0.0 || yp<=0.0 || !xp.is_finite() || !yp.is_finite() {return None;}
    let reference=yp/xp;
    if reference.is_finite() && reference>0.0 {Some(reference)} else {None}
}
#[cfg(test)]
mod tests {
 use super::*;
 #[test] fn sell_post_reserves(){assert_eq!(post_reference(100.0,100.0,1.0,0.9,false),Some(99.1/101.0));}
 #[test] fn buy_post_reserves(){assert_eq!(post_reference(100.0,100.0,1.0,1.1,true),Some(101.1/99.0));}
 #[test] fn zero_invalid(){assert!(post_reference(0.0,1.0,1.0,1.0,true).is_none());}
 #[test] fn negative_invalid(){assert!(post_reference(100.0,100.0,-1.0,1.0,false).is_none());}
 #[test] fn nan_invalid(){assert!(post_reference(100.0,f64::NAN,1.0,1.0,false).is_none());}
 #[test] fn infinity_invalid(){assert!(post_reference(f64::INFINITY,100.0,1.0,1.0,false).is_none());}
 #[test] fn depleted_token(){assert!(post_reference(1.0,100.0,1.0,1.0,true).is_none());}
 #[test] fn depleted_quote(){assert!(post_reference(100.0,1.0,1.0,1.0,false).is_none());}
 #[test] fn excessive_delta(){assert!(post_reference(100.0,1.0,1.0,2.0,false).is_none());}
 #[test] fn tiny_finite_reference(){assert!(post_reference(1e10,1e-5,1.0,1e-10,false).unwrap()>0.0);}
 #[test] fn overflow_guard(){assert!(post_reference(1e308,1e308,1e308,1.0,false).is_none());}
 #[test] fn horizons_unique_sorted(){assert!(HORIZONS.windows(2).all(|p|p[0]<p[1]));assert_eq!(HORIZONS[9],300000);}
}
