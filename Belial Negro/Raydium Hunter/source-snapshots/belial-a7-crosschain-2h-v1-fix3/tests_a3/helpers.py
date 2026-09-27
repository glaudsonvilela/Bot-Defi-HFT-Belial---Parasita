"""Explicit synthetic test instruments. Never used by the executable's live mode."""
from array import array
import json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'n2_a2/tools'))
class FakeCore:
    info={'device_name':'SYNTHETIC_TEST_BACKEND_NOT_GPU'}
    def __init__(self):self.weights=array('f',[0.]*64);self.train_calls=0
    def predict_batch(self,pool,bases,dim,heads):return [[1/(1+math.exp(-self.weights[(pool+head)%64])) for head in range(heads)] for _ in bases]
    def train(self,pool,head,base,dim,label,lr,l2):
        p=self.predict_batch(pool,[base],dim,head+1)[0][head];self.weights[(pool+head)%64]+=float((label-p)*lr);self.train_calls+=1;return p
    def read_weights(self):return array('f',self.weights)
    def write_weights(self,v):self.weights=array('f',v)
    def close(self):pass
class J:
    def __init__(self):self.rows=[]
    def write(self,name,obj,fsync=False):self.rows.append((name,obj,fsync))
def swap(t,p=1.,sig='x',symbol='FARTCOIN'):
    return {'schema':'belial_m2_swap_v1','received_unix_ms':t,'reference_post_price':p,'quote_sol':1.,'pool_quote_reserve_sol':1000.,'side':'SELL','signature':sig,'symbol':symbol,'a2_available_ms':t}
def ex(t,ident='x|w|z',symbol='FARTCOIN'):
    return {'symbol':symbol,'event_id':ident,'event_unix_ms':t-100,'signal_available_unix_ms':t,'a2_available_ms':t,'sell_quote_sol':2.5,'cluster_sell_quote_sol_10s':5.5,'cluster_size':2,'cluster_unique_wallets_10s':2,'actor_resolution_latency_ms':100,'impact_score':30,'absorption_score':0,'inventory_exit_pct':100,'observed_inventory_pool_pct':.01,'leader_quality_120s_at_entry':0,'leader_samples_120s_at_entry':0,'pre_return_30s_bps':0,'pre_sell_pressure_30s_pct':50,'regime':'MIXED','observed_entry_price':1.,'entry_price':1.}
def history(ob,t,symbol='FARTCOIN'):
    for k in range(10):ob.feed_swap(swap(t-9000+k*900,1.,str(k),symbol),t)
def append(path,row):
    with Path(path).open('a') as f:f.write(json.dumps(row,separators=(',',':'))+'\n');f.flush()
