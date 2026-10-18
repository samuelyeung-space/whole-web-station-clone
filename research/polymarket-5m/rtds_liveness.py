"""RTDS acquisition supervision; no trading, pricing model or replay changes."""
from __future__ import annotations
import asyncio
import json
import math

TOPICS={'rtds':'crypto_prices_chainlink','rtds-binance':'crypto_prices'}


def required_coins(trader):
    coins=tuple(getattr(trader,'coins',('btc',)))
    return tuple(c for c in getattr(trader,'trade_coins',coins) if c in coins) or coins


def positive_number(value):
    if isinstance(value,bool):return None
    try:value=float(value)
    except (TypeError,ValueError):return None
    return value if math.isfinite(value) and value>0 else None


class PriceProgress:
    def __init__(self,stream,coins,required_coins,idle_seconds=30.0):
        self.stream=stream;self.coins=tuple(coins);self.required=tuple(required_coins)
        if stream not in TOPICS or not self.required or not set(self.required)<=set(self.coins):
            raise ValueError('explicit required RTDS symbols must be recorded')
        if positive_number(idle_seconds) is None:raise ValueError('positive idle deadline required')
        self.idle_seconds=float(idle_seconds);self.highwater={};self.last_progress={}
        self.last_receipt_ms={};self.started=0.0;self.epoch=0;self.active=False

    def opened(self,monotonic):
        self.started=monotonic;self.epoch+=1;self.active=True
        self.last_progress={};self.last_receipt_ms={}
        # Preserve source highwater across reconnects: replaying a stale initial
        # sample must never buy another fresh-data interval.

    def observe(self,text,monotonic,receipt_ms):
        try:msg=json.loads(text)
        except (TypeError,ValueError):return False
        if not isinstance(msg,dict) or msg.get('topic')!=TOPICS[self.stream] or msg.get('type')!='update':return False
        payload=msg.get('payload')
        if not isinstance(payload,dict):return False
        symbol=str(payload.get('symbol','')).lower().strip()
        suffix='/usd' if self.stream=='rtds' else 'usdt'
        coin=next((c for c in self.coins if symbol==c+suffix),None)
        stamp=positive_number(payload.get('timestamp'));value=positive_number(payload.get('value'))
        if coin is None or stamp is None or value is None or stamp<=self.highwater.get(coin,0):return False
        self.highwater[coin]=stamp;self.last_progress[coin]=monotonic;self.last_receipt_ms[coin]=receipt_ms
        return True

    def expired(self,monotonic):
        return [c for c in self.required if monotonic-self.last_progress.get(c,self.started)>=self.idle_seconds]

    def next_deadline(self):
        return min(self.last_progress.get(c,self.started)+self.idle_seconds for c in self.required)

    def snapshot(self,monotonic):
        return dict(connection_epoch=self.epoch,active=self.active,required_coins=list(self.required),
            idle_seconds=self.idle_seconds,coins={c:dict(last_progress_receipt_ms=self.last_receipt_ms.get(c),
                progress_age_ms=(monotonic-self.last_progress[c])*1000 if c in self.last_progress else None,
                available=self.active and c in self.last_progress and monotonic-self.last_progress[c]<self.idle_seconds)
                for c in self.coins})


async def supervise_connection(ws,state,on_message,receipt_clock,ping_seconds=5.0,send_timeout=10.0):
    """Return on clean close; raise for staleness or sender/receiver failure.

    Every frame still reaches the original recorder handler, including frames
    that do not count as live progress. Deadlines use only the monotonic clock.
    """
    loop=asyncio.get_running_loop()
    async def receive():
        async for text in ws:
            receipt=receipt_clock()
            on_message(text,receipt)
            state.observe(text,loop.time(),receipt)
        return 'clean_close'
    async def ping():
        while True:
            await asyncio.sleep(ping_seconds)
            await asyncio.wait_for(ws.send('ping'),timeout=send_timeout)
    async def freshness():
        while True:
            now=loop.time();missing=state.expired(now)
            if missing:raise TimeoutError('RTDS live update deadline exceeded: '+','.join(missing))
            await asyncio.sleep(max(0,state.next_deadline()-now))
    tasks=[asyncio.create_task(receive(),name='rtds-receive'),
           asyncio.create_task(ping(),name='rtds-ping'),
           asyncio.create_task(freshness(),name='rtds-freshness')]
    try:
        done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        # Surface every completed failure before accepting a simultaneous clean close.
        for t in tasks:
            if t in done and t.exception() is not None:raise t.exception()
        for t in tasks:
            if t in done:
                result=t.result()
                if result=='clean_close':return result
                raise RuntimeError('RTDS supervisor child stopped unexpectedly')
    finally:
        for t in tasks:
            if not t.done():t.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)


async def reconnecting_rtds(trader,stream,connect,endpoint,subscriptions,receipt_clock):
    loop=asyncio.get_running_loop()
    states=getattr(trader,'rtds_liveness',None)
    if states is None:states={};trader.rtds_liveness=states
    required=required_coins(trader)
    state=states.setdefault(stream,PriceProgress(stream,trader.coins,required))
    failures=0
    while True:
        reason='connection_failed';opened=False
        try:
            async with connect(endpoint,ping_interval=None,max_size=None,open_timeout=10,close_timeout=5) as ws:
                state.opened(loop.time());opened=True
                trader.rec.write('errors',dict(at=receipt_clock(),where=stream+'-open',
                    **state.snapshot(loop.time())))
                await asyncio.wait_for(ws.send(json.dumps({'action':'subscribe','subscriptions':subscriptions})),timeout=10)
                reason=await supervise_connection(ws,state,lambda text,at:trader.on_rtds(text,at,stream),receipt_clock)
        except asyncio.CancelledError:
            reason='recorder_stop';raise
        except Exception as error:
            reason=repr(error)
        finally:
            state.active=False;snapshot=state.snapshot(loop.time())
            trader.rec.write('errors',dict(at=receipt_clock(),where=stream,err=reason,
                connection_opened=opened,**snapshot,connection_active=False))
        # Bounded backoff limits retries when a legacy source is unavailable.
        progressed=all(c in state.last_progress for c in state.required) if opened else False
        failures=0 if progressed else min(failures+1,5)
        await asyncio.sleep(min(30,2**max(1,failures)))
