"""Timer-indexed equivalent of BatchGates; no signal/quote rules changed."""
from collections import deque
import heapq
from book_batch_intake import BatchGates


class FastBatchGates(BatchGates):
    def __init__(self,arms):
        super().__init__(arms)
        self.expiries=[]

    def before_group(self,now,epoch,lifecycle=False):
        while self.pending and self.pending[0]['decision_ms']<=now:
            r=self.pending.popleft();key=r['arm_id'];asset=r['asset_id']
            template=self.g.books.get(asset)
            if template:
                self.g.books[key]=dict(template,history=deque(template['history']))
            mid=template['quote']['mid'] if template else .5
            if self.g.arm(key,1,mid,r['decision_ms'],epoch=self.epoch):
                self.active[asset].add(key)
                heapq.heappush(self.expiries,(r['decision_ms']+self.g.config.deadline_ms,key))
        while self.expiries and self.expiries[0][0]<=now:
            deadline,key=heapq.heappop(self.expiries)
            state=self.g.arms[key]
            if state['status']=='armed':
                state['status']='timeout_unfilled'
                self.g._record(key,'timeout_unfilled',deadline)
        if lifecycle or epoch!=self.epoch:
            for key in list(self.g.books):self.g.disconnect(key,now,'source_lifecycle')
        self.epoch=epoch
        for asset,keys in list(self.active.items()):
            for key in list(keys):
                if self.g.arms[key]['status']!='armed':
                    keys.remove(key);self.g.books.pop(key,None)
            if not keys:self.active.pop(asset,None)

    def observe(self,obs):
        super().observe(obs)
        if not self.active[obs['asset_id']]:self.active.pop(obs['asset_id'],None)


if __name__=='__main__':
    import book_batch_intake
    book_batch_intake.BatchGates=FastBatchGates
    book_batch_intake.main()
