"""Offline RTDS liveness contracts; fake sockets never open a network connection."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest import mock

import rtds_liveness as rtds


CLOSE = object()


def update(*, stream="rtds", symbol=None, timestamp=1000, value=123.0, **extra):
    symbol = symbol or ("btc/usd" if stream == "rtds" else "btcusdt")
    return json.dumps(dict(topic=rtds.TOPICS[stream], type="update",
                          payload=dict(symbol=symbol, timestamp=timestamp, value=value), **extra))


class FakeSocket:
    def __init__(self, frames=(), *, ping_error=None, block_ping=False):
        self.frames = asyncio.Queue()
        for frame in frames:
            self.frames.put_nowait(frame)
        self.sent = []
        self.ping_error = ping_error
        self.block_ping = block_ping
        self.receive_cancelled = False
        self.send_cancelled = False
        self.exited = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            frame = await self.frames.get()
        except asyncio.CancelledError:
            self.receive_cancelled = True
            raise
        if frame is CLOSE:
            raise StopAsyncIteration
        if isinstance(frame, BaseException):
            raise frame
        return frame

    async def send(self, frame):
        self.sent.append(frame)
        if frame == "ping":
            if self.ping_error is not None:
                raise self.ping_error
            if self.block_ping:
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    self.send_cancelled = True
                    raise


class FakeConnection:
    def __init__(self, socket=None, *, error=None, entered=None, block_open=False):
        self.socket = socket
        self.error = error
        self.entered = entered
        self.block_open = block_open

    async def __aenter__(self):
        if self.entered is not None:
            self.entered.set()
        if self.block_open:
            await asyncio.Future()
        if self.error is not None:
            raise self.error
        return self.socket

    async def __aexit__(self, *args):
        self.socket.exited.set()
        return False


class MemoryRecorder:
    def __init__(self):
        self.rows = []

    def write(self, stream, row):
        self.rows.append((stream, row))


class ProgressTests(unittest.TestCase):
    def test_effective_required_coins_match_the_recorded_configuration(self):
        for trader, expected in (
            (SimpleNamespace(coins=('btc','eth'),trade_coins=('btc',)), ('btc',)),
            (SimpleNamespace(coins=('eth',),trade_coins=('btc',)), ('eth',)),
            (SimpleNamespace(coins=('btc','eth'),trade_coins=('eth','sol')), ('eth',)),
            (SimpleNamespace(coins=('btc','eth'),trade_coins=()), ('btc','eth')),
        ):
            with self.subTest(trader=trader):
                self.assertEqual(rtds.required_coins(trader), expected)

    def state(self, stream="rtds", coins=("btc", "eth"), required=("btc",)):
        state = rtds.PriceProgress(stream, coins, required)
        state.opened(10)
        return state

    def test_required_deadline_is_monotonic_and_optional_coin_is_independent(self):
        state = self.state()
        self.assertEqual(state.expired(39.999), [])
        self.assertEqual(state.expired(40), ["btc"])
        self.assertTrue(state.observe(update(timestamp=1000), 39, -100000))
        self.assertEqual(state.expired(40), [])
        self.assertEqual(state.expired(69), ["btc"])
        snapshot = state.snapshot(40)
        self.assertTrue(snapshot["coins"]["btc"]["available"])
        self.assertFalse(snapshot["coins"]["eth"]["available"])
        self.assertIsNone(snapshot["coins"]["eth"]["last_progress_receipt_ms"])
        self.assertEqual(snapshot["coins"]["btc"]["last_progress_receipt_ms"], -100000)

    def test_all_required_coins_must_progress_not_just_one_active_coin(self):
        state = self.state(required=("btc", "eth"))
        state.observe(update(timestamp=1), 39, 1)
        self.assertEqual(state.expired(40), ["eth"])
        state.observe(update(symbol="eth/usd", timestamp=2), 40, 2)
        self.assertEqual(state.expired(40), [])

    def test_control_irrelevant_and_malformed_frames_do_not_refresh(self):
        bad = ["", "pong", "PONG", "{", "[]", "null", "true",
               json.dumps({"type": "subscribed"}),
               update(symbol="sol/usd"),
               json.dumps({"topic": "crypto_prices", "type": "update", "payload": {
                   "symbol": "btcusdt", "timestamp": 1000, "value": 123}}),
               json.dumps({"topic": "crypto_prices_chainlink", "type": "subscribe", "payload": {
                   "symbol": "btc/usd", "data": [{"timestamp": 1000, "value": 123}]}}),
               json.dumps({"topic": "crypto_prices_chainlink", "type": "update", "payload": []})]
        for text in bad:
            with self.subTest(text=text):
                state = self.state()
                self.assertFalse(state.observe(text, 39, 90000))
                self.assertEqual(state.expired(40), ["btc"])
                self.assertEqual(state.highwater, {})

    def test_bool_nan_infinity_nonpositive_or_missing_numbers_do_not_refresh(self):
        invalid = [True, False, None, "not-a-number", float("nan"),
                   float("inf"), float("-inf"), 0, -1, {}, []]
        for field in ("timestamp", "value"):
            for value in invalid:
                with self.subTest(field=field, value=value):
                    state = self.state()
                    kwargs = {field: value}
                    self.assertFalse(state.observe(update(**kwargs), 39, 5000))
                    self.assertEqual(state.expired(40), ["btc"])

    def test_duplicate_and_regressing_timestamps_do_not_refresh_across_reconnect(self):
        state = self.state()
        self.assertTrue(state.observe(update(timestamp=1000), 11, 1001))
        self.assertFalse(state.observe(update(timestamp=1000, value=999), 39, 1002))
        self.assertFalse(state.observe(update(timestamp=999), 39, 1003))
        self.assertEqual(state.expired(41), ["btc"])
        state.opened(50)
        self.assertEqual(state.epoch, 2)
        self.assertEqual(state.highwater["btc"], 1000)
        self.assertFalse(state.observe(update(timestamp=1000), 79, 1004))
        self.assertFalse(state.snapshot(79)["coins"]["btc"]["available"])
        self.assertEqual(state.expired(80), ["btc"])
        self.assertTrue(state.observe(update(timestamp=1001), 80, 1005))
        self.assertEqual(state.expired(80), [])

    def test_unchanged_price_with_fresh_timestamp_is_progress(self):
        for stream in rtds.TOPICS:
            with self.subTest(stream=stream):
                state = self.state(stream)
                self.assertTrue(state.observe(update(stream=stream, timestamp=1000, value=123), 11, 1))
                self.assertTrue(state.observe(update(stream=stream, timestamp=1001, value=123), 39, 2))
                self.assertEqual(state.expired(41), [])

    def test_quote_currency_must_match_the_expected_source_symbol(self):
        # A USD update must not certify the USDT subscription, or conversely.
        for stream, wrong_symbol in (("rtds", "btcusdt"), ("rtds-binance", "btc/usd")):
            with self.subTest(stream=stream):
                state = self.state(stream)
                self.assertFalse(state.observe(update(stream=stream, symbol=wrong_symbol), 39, 1))
                self.assertEqual(state.expired(40), ["btc"])

    def test_invalid_required_contracts_and_idle_deadlines_are_rejected(self):
        for args in (("unknown", ("btc",), ("btc",)), ("rtds", ("btc",), ()),
                     ("rtds", ("btc",), ("eth",))):
            with self.subTest(args=args), self.assertRaises(ValueError):
                rtds.PriceProgress(*args)
        for idle in (True, 0, -1, float("nan"), float("inf")):
            with self.subTest(idle=idle), self.assertRaises(ValueError):
                rtds.PriceProgress("rtds", ("btc",), ("btc",), idle_seconds=idle)


class SupervisionTests(unittest.IsolatedAsyncioTestCase):
    def state(self, idle=.03):
        state = rtds.PriceProgress("rtds", ("btc",), ("btc",), idle_seconds=idle)
        state.opened(asyncio.get_running_loop().time())
        return state

    async def assert_no_helpers(self):
        await asyncio.sleep(0)
        helpers = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()
                   and task.get_name() in {"rtds-receive", "rtds-ping", "rtds-freshness"}
                   and not task.done()]
        self.assertEqual(helpers, [])

    async def test_silent_receiver_times_out_and_cleans_up(self):
        socket = FakeSocket()
        with self.assertRaisesRegex(TimeoutError, "btc"):
            await asyncio.wait_for(rtds.supervise_connection(socket, self.state(), lambda *a: None,
                                                            lambda: 1, ping_seconds=10), .5)
        self.assertTrue(socket.receive_cancelled)
        await self.assert_no_helpers()

    async def test_continuing_pongs_do_not_conceal_no_data_and_raw_callbacks_are_kept(self):
        socket = FakeSocket()
        received = []
        async def pongs():
            while True:
                socket.frames.put_nowait("pong")
                await asyncio.sleep(.002)
        producer = asyncio.create_task(pongs())
        try:
            with self.assertRaisesRegex(TimeoutError, "btc"):
                await asyncio.wait_for(rtds.supervise_connection(socket, self.state(),
                    lambda *args: received.append(args), lambda: 42, ping_seconds=10), .5)
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
        self.assertGreater(len(received), 2)
        self.assertTrue(all(args == ("pong", 42) for args in received))
        await self.assert_no_helpers()

    async def test_sender_failure_surfaces_without_waiting_for_receiver(self):
        socket = FakeSocket(ping_error=OSError("synthetic ping failure"))
        with self.assertRaisesRegex(OSError, "synthetic ping failure"):
            await asyncio.wait_for(rtds.supervise_connection(socket, self.state(idle=1),
                lambda *a: None, lambda: 1, ping_seconds=.001), .5)
        self.assertEqual(socket.sent, ["ping"])
        self.assertTrue(socket.receive_cancelled)
        await self.assert_no_helpers()

    async def test_blocked_sender_is_timed_out_and_awaited(self):
        socket = FakeSocket(block_ping=True)
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(rtds.supervise_connection(socket, self.state(idle=1),
                lambda *a: None, lambda: 1, ping_seconds=.001, send_timeout=.005), .5)
        self.assertTrue(socket.send_cancelled)
        self.assertTrue(socket.receive_cancelled)
        await self.assert_no_helpers()

    async def test_clean_close_and_every_raw_frame_reach_the_legacy_handler(self):
        frames = ["pong", "{", json.dumps({"ack": "subscribed"}), update(symbol="eth/usd"),
                  update(timestamp=1000), update(timestamp=1000), update(timestamp=1001)]
        socket = FakeSocket([*frames, CLOSE])
        received = []
        result = await rtds.supervise_connection(socket, self.state(idle=1),
            lambda *args: received.append(args), lambda: 777, ping_seconds=10)
        self.assertEqual(result, "clean_close")
        self.assertEqual(received, [(text, 777) for text in frames])
        await self.assert_no_helpers()

    async def test_receiver_and_legacy_handler_failures_propagate(self):
        socket = FakeSocket([ConnectionError("synthetic receive failure")])
        with self.assertRaisesRegex(ConnectionError, "synthetic receive failure"):
            await rtds.supervise_connection(socket, self.state(idle=1), lambda *a: None, lambda: 1)
        def broken_handler(*args):
            raise ValueError("synthetic handler failure")
        with self.assertRaisesRegex(ValueError, "synthetic handler failure"):
            await rtds.supervise_connection(FakeSocket([update()]), self.state(idle=1),
                                           broken_handler, lambda: 1)
        await self.assert_no_helpers()

    async def test_simultaneous_clean_close_cannot_hide_expired_data_deadline(self):
        state = self.state()
        state.opened(asyncio.get_running_loop().time() - 1)
        with self.assertRaisesRegex(TimeoutError, "deadline exceeded"):
            await rtds.supervise_connection(FakeSocket([CLOSE]), state,
                                           lambda *args: None, lambda: 1)
        await self.assert_no_helpers()

    async def test_one_stream_can_recover_while_the_other_stream_times_out(self):
        loop = asyncio.get_running_loop()
        chainlink_state = self.state(idle=.025)
        binance_state = rtds.PriceProgress("rtds-binance", ("btc",), ("btc",), idle_seconds=.025)
        binance_state.opened(loop.time())
        silent_socket, healthy_socket = FakeSocket(), FakeSocket()
        healthy_frames = []
        async def live_updates():
            stamp = 1000
            while True:
                healthy_socket.frames.put_nowait(update(stream="rtds-binance", timestamp=stamp))
                stamp += 1
                await asyncio.sleep(.003)
        producer = asyncio.create_task(live_updates())
        failing = asyncio.create_task(rtds.supervise_connection(silent_socket, chainlink_state,
            lambda *args: None, lambda: 1, ping_seconds=10))
        healthy = asyncio.create_task(rtds.supervise_connection(healthy_socket, binance_state,
            lambda *args: healthy_frames.append(args), lambda: 2, ping_seconds=10))
        try:
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(failing, .5)
            self.assertFalse(healthy.done())
            self.assertGreater(len(healthy_frames), 2)
            self.assertFalse(chainlink_state.snapshot(loop.time())["coins"]["btc"]["available"])
            self.assertTrue(binance_state.snapshot(loop.time())["coins"]["btc"]["available"])
            healthy_socket.frames.put_nowait(CLOSE)
            self.assertEqual(await asyncio.wait_for(healthy, .5), "clean_close")
        finally:
            for task in (producer, failing, healthy):
                if not task.done():
                    task.cancel()
            await asyncio.gather(producer, failing, healthy, return_exceptions=True)
        await self.assert_no_helpers()

    async def test_outer_cancellation_cleans_up_reader_and_inflight_send(self):
        socket = FakeSocket(block_ping=True)
        task = asyncio.create_task(rtds.supervise_connection(socket, self.state(idle=1),
            lambda *a: None, lambda: 1, ping_seconds=.001))
        for _ in range(100):
            if socket.sent:
                break
            await asyncio.sleep(.001)
        self.assertEqual(socket.sent, ["ping"])
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(socket.receive_cancelled)
        self.assertTrue(socket.send_cancelled)
        await self.assert_no_helpers()


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    def trader(self, *, idle=.015):
        state = rtds.PriceProgress("rtds", ("btc", "eth"), ("btc",), idle_seconds=idle)
        raw = []
        trader = SimpleNamespace(coins=("btc", "eth"), trade_coins=("btc",),
                                 rec=MemoryRecorder(), rtds_liveness={"rtds": state})
        trader.on_rtds = lambda *args: raw.append(args)
        return trader, state, raw

    async def test_timeout_closes_then_resubscribes_and_recovers(self):
        trader, state, raw = self.trader()
        first = FakeSocket()
        good_frame = update(timestamp=2000)
        second = FakeSocket([good_frame, CLOSE])
        third_entered = asyncio.Event()
        contexts = iter([FakeConnection(first), FakeConnection(second),
                         FakeConnection(entered=third_entered, block_open=True)])
        calls = []
        def connect(*args, **kwargs):
            calls.append((args, kwargs))
            return next(contexts)
        real_sleep = asyncio.sleep
        backoffs = []
        async def fast_backoff(seconds):
            if seconds in (2, 4, 8, 16, 30):
                backoffs.append(seconds)
                await real_sleep(0)
            else:
                await real_sleep(seconds)
        with mock.patch.object(rtds.asyncio, "sleep", fast_backoff):
            task = asyncio.create_task(rtds.reconnecting_rtds(trader, "rtds", connect,
                "offline://rtds", [{"topic": "crypto_prices_chainlink", "type": "*"}], lambda: 12345))
            try:
                await asyncio.wait_for(third_entered.wait(), .5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(first.exited.is_set())
        self.assertTrue(second.exited.is_set())
        self.assertEqual(raw, [(good_frame, 12345, "rtds")])
        self.assertEqual(first.sent, second.sent)
        subscription = json.loads(first.sent[0])
        self.assertEqual(subscription, {"action": "subscribe", "subscriptions": [
            {"topic": "crypto_prices_chainlink", "type": "*"}]})
        self.assertEqual(backoffs, [2, 2])
        self.assertEqual(state.highwater, {"btc": 2000})
        self.assertFalse(state.active)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(kw == dict(ping_interval=None, max_size=None, open_timeout=10,
                                      close_timeout=5) for _, kw in calls))
        rows = [row for _, row in trader.rec.rows]
        self.assertEqual([row["connection_epoch"] for row in rows if row["where"] == "rtds-open"], [1, 2])
        closes = [row for row in rows if row["where"] == "rtds"]
        self.assertIn("deadline exceeded", closes[0]["err"])
        self.assertEqual(closes[1]["err"], "clean_close")
        self.assertEqual(closes[2]["err"], "recorder_stop")
        self.assertTrue(all(row["connection_active"] is False for row in closes))
        self.assertTrue(all(row["active"] is False for row in closes))

    async def test_repeated_open_failures_have_bounded_backoff_and_cancel_cleanly(self):
        trader, state, raw = self.trader()
        calls = []
        backoffs = []
        enough = asyncio.Event()
        def connect(*args, **kwargs):
            calls.append((args, kwargs))
            return FakeConnection(error=OSError("synthetic unavailable source"))
        real_sleep = asyncio.sleep
        async def fast_backoff(seconds):
            backoffs.append(seconds)
            if len(backoffs) == 6:
                enough.set()
                await asyncio.Future()
            await real_sleep(0)
        with mock.patch.object(rtds.asyncio, "sleep", fast_backoff):
            task = asyncio.create_task(rtds.reconnecting_rtds(trader, "rtds", connect,
                "offline://rtds", [], lambda: 1))
            try:
                await asyncio.wait_for(enough.wait(), .5)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(backoffs, [2, 4, 8, 16, 30, 30])
        self.assertEqual(len(calls), 6)
        self.assertEqual(raw, [])
        self.assertFalse(state.active)
        self.assertTrue(all(not row["connection_opened"] for _, row in trader.rec.rows))


if __name__ == "__main__":
    unittest.main()
