"""Quote-only fanout of fixed parent arms, using existing atomic books/gates.

Copy the past-only template history on arm creation: overlapping watches never
share mutable deques. No parent signal, feature, deadline or threshold changes.
"""
import argparse,gzip,hashlib,json
from collections import Counter,defaultdict,deque
from pathlib import Path
from atomic_quote_groups import AtomicBooks,receipt_groups
from book_balance_vacuum import QuoteGates
from h_replay_archive import _iter_json_objects


class BatchGates:
    def __init__(self,arms):
        if len({r['arm_id'] for r in arms})!=len(arms):raise ValueError('duplicate arm')
        self.pending=deque(sorted(arms,key=lambda r:(r['decision_ms'],r['arm_id'])))
        self.arms={r['arm_id']:r for r in arms};self.g=QuoteGates()
        self.active=defaultdict(set);self.epoch=None;self.gates=[]

    def before_group(self,now,epoch,lifecycle=False):
        while self.pending and self.pending[0]['decision_ms']<=now:
            r=self.pending.popleft();key=r['arm_id'];asset=r['asset_id']
            template=self.g.books.get(asset)
            if template:
                self.g.books[key]=dict(template,history=deque(template['history']))
            mid=template['quote']['mid'] if template else .5
            if self.g.arm(key,1,mid,r['decision_ms'],epoch=self.epoch):self.active[asset].add(key)
        self.g.advance_time(now)
        if lifecycle or epoch!=self.epoch:
            for key in list(self.g.books):self.g.disconnect(key,now,'source_lifecycle')
        self.epoch=epoch
        for asset,keys in self.active.items():
            for key in list(keys):
                if self.g.arms[key]['status']!='armed':keys.remove(key);self.g.books.pop(key,None)

    def observe(self,obs):
        asset=obs['asset_id'];book=obs['book'];now=obs['recv_ms']
        args=dict(update_id=obs['update_id'],validated=obs['validated'],ambiguous_receipt=obs['ambiguous_receipt'])
        self.g.on_book(asset,now,obs['epoch'],book.bids,book.asks,**args)
        for key in list(self.active[asset]):
            events=self.g.on_book(key,now,obs['epoch'],book.bids,book.asks,**args)
            for e in events:
                r=self.arms[key]
                self.gates.append(dict(e,arm_id=key,market_id=r['market_id'],side=r['side'],
                    asset_id=asset,parent_decision_ms=r['decision_ms'],ask=min(book.asks),
                    parent_ids=r.get('parent_ids',[]),pnl=None))
            if self.g.arms[key]['status']!='armed':
                self.active[asset].remove(key);self.g.books.pop(key,None)


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--protocol',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    protocol=json.load(open(a.protocol))
    if sha(protocol['raw_path'])!=protocol['raw_sha256']:raise ValueError('raw identity mismatch')
    for n,h in protocol['code_sha256'].items():
        if sha(Path(__file__).parent/n)!=h:raise ValueError('code identity mismatch')
    arms=protocol['arms'];dispatcher=BatchGates(arms);books=AtomicBooks({r['asset_id'] for r in arms});counts=Counter()
    stop=max(r['decision_ms'] for r in arms)+30000;observed=None
    for group in receipt_groups(r for _,r in _iter_json_objects(protocol['raw_path'])):
        now=group[0]['recv_ms']
        if now>stop:
            dispatcher.g.advance_time(stop);break
        observed=now;counts['rows']+=len(group);counts['receipt_groups']+=1
        lifecycle=any(r['kind'] in ('clob_connection','clob_error') for r in group)
        dispatcher.before_group(now,group[0]['connection_epoch'],lifecycle)
        for obs in books.apply(group):
            counts['selected_asset_groups']+=1
            dispatcher.observe(obs)
    else:
        if observed is not None:dispatcher.g.advance_time(observed)
    fired={e['arm_id'] for e in dispatcher.gates}
    result=dict(schema='book-batch-intake-v1',protocol_sha256=sha(a.protocol),
        arm_count=len(arms),parent_ids=sorted({p for r in arms for p in r.get('parent_ids',[])}),
        counts=dict(counts),observed_until_ms=observed,
        gate_counts=dict(Counter(e['status'] for e in dispatcher.gates)),gate_events=dispatcher.gates,
        fired_arm_count=len(fired),no_gate_arm_count=len(arms)-len(fired),
        no_gate_status_counts=dict(Counter(s['status'] for k,s in dispatcher.g.arms.items() if k not in fired)),
        audit=dispatcher.g.audit(),financial_replays=0,economic_pnl=None,formal_oos_result=None,full_week_pnl=None)
    Path(a.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('audit','gate_events','parent_ids')}))


if __name__=='__main__':main()
