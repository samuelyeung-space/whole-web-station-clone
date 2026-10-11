"""Constraint provenance and clock tests; no trading profitability assertion."""
import pytest

from mine500_constraint_extract import extract_constraints, market_maps, merge_constraints


def markets():
    return {"m":{"market_id":"m","recv_ms":100,"start_ms":0,"end_ms":300000,
                 "up_token_id":"u","down_token_id":"d"}}


def tick(now, value, *, token="u", seq=None, source=None):
    return {"kind":"clob_snapshot","market_id":"m","asset_id":token,"recv_ms":now,
            "source_ts_ms":now if source is None else source,"seq":seq,"tick_size":value}


def test_gamma_latest_is_never_backfilled_and_gamma_tick_is_ignored():
    gamma = [dict(conditionId="m",_recorded_at_ms=300,orderMinSize=10,orderPriceMinTickSize=.001),
             dict(conditionId="m",_recorded_at_ms=100,orderMinSize=5,orderPriceMinTickSize=.01)]
    result, audit = extract_constraints(markets(),gamma,[])
    assert [(r["recv_ms"],r["min_order_size"]) for r in result] == [(100,5),(300,10)]
    assert all("up_tick_size" not in row for row in result)
    assert audit["gamma_tick_used"] is False
    assert result[0]["min_order_size_available_ms"] == 100


def test_missing_receipt_and_unknown_market_not_inferred():
    gamma = [{"conditionId":"m","orderMinSize":5},
             {"conditionId":"m","_recorded_at_ms":99,"orderMinSize":5},
             {"conditionId":"unknown","_recorded_at_ms":100,"orderMinSize":5}]
    result, audit = extract_constraints(markets(),gamma,[tick(99,.01)])
    assert not result
    assert audit["counts"]["gamma_missing_local_receipt"] == 1


def test_token_specific_ticks_and_explicit_change_have_own_clocks():
    rows=[tick(101,.01),tick(102,.001,token="d"),
          dict(kind="clob_tick_size_change",market_id="m",asset_id="u",recv_ms=105,
               source_ts_ms=104,new_tick_size="0.001",old_tick_size="0.01",seq=3)]
    result,audit=extract_constraints(markets(),[],rows)
    assert result[0]["up_tick_size"] == .01 and "down_tick_size" not in result[0]
    assert result[1]["down_tick_size"] == .001 and "up_tick_size" not in result[1]
    assert result[2]["up_tick_size"] == .001
    assert result[2]["up_tick_size_available_ms"] == 105
    assert audit["counts"]["explicit_tick_changes"] == 1
    assert audit["tick_update_stream_complete"] is False


def test_regressed_venue_tick_cannot_rewind_current_tick():
    rows=[tick(110,.01,source=109),
          dict(kind="clob_price_change",market_id="m",asset_id="u",recv_ms=120,source_ts_ms=119),
          tick(130,.001,source=110)]
    result,audit=extract_constraints(markets(),[],rows)
    assert len(result)==1 and result[0]["up_tick_size"] == .01
    assert audit["counts"]["regressed_source_rows_ignored"] == 1


def test_disconnect_invalidates_then_new_epoch_can_reset_source_clock():
    rows=[tick(101,.01),dict(kind="clob_error",recv_ms=102,seq=2),
          dict(kind="clob_connection",recv_ms=103,seq=3),tick(104,.001,source=50)]
    result,_=extract_constraints(markets(),[],rows)
    assert [row["up_tick_size"] for row in result] == [.01,None,.001]


def test_exact_clock_conflict_requires_proven_sequence():
    result,audit=extract_constraints(markets(),[],[tick(110,.01),tick(110,.001)])
    assert result[0]["up_tick_size"] is None
    assert result[0]["constraint_ambiguous_fields"] == ["up_tick_size"]
    assert audit["counts"]["ambiguous_exact_time_fields"] == 1
    result,_=extract_constraints(markets(),[],[tick(110,.01,seq=1),tick(110,.001,seq=2)])
    assert result[0]["up_tick_size"] == .001


def test_cross_stream_same_millisecond_order_is_not_assumed():
    raw=[{"recv_ms":110,"msg":{"event_type":"tick_size_change","market":"m",
         "asset_id":"u","timestamp":"110","new_tick_size":"0.001"}}]
    result,_=extract_constraints(markets(),[],[tick(110,.01,seq=1)],raw)
    assert result[0]["up_tick_size"] is None


def test_raw_tick_change_retains_receipt_not_event_clock():
    raw=[{"recv_ms":120,"msg":[{"event_type":"tick_size_change","market":"m",
         "asset_id":"u","timestamp":"115","new_tick_size":"0.001"}]}]
    result,_=extract_constraints(markets(),[],[tick(110,.01)],raw)
    assert result[-1]["up_tick_size"] == .001
    assert result[-1]["up_tick_size_effective_ms"] == 120


@pytest.mark.parametrize("invalid", [True,0,-.1,"bad",float("nan"),1])
def test_invalid_tick_explicitly_invalidates_prior_value(invalid):
    result,_=extract_constraints(markets(),[],[tick(110,.01),tick(120,invalid)])
    assert result[-1]["up_tick_size"] is None


def test_bad_market_mapping_or_order_fails_closed():
    with pytest.raises(ValueError,match="receipt clock"):
        extract_constraints(markets(),[],[tick(120,.01),tick(110,.001)])
    row=tick(110,.01);row["market_id"]="other"
    with pytest.raises(ValueError,match="identity"):
        extract_constraints(markets(),[],[row])


def test_merge_records_same_clock_assumption_and_preserves_book_decision_order():
    normalized=[{"type":"dataset","recv_ms":0},
                {"type":"market","recv_ms":100,"market_id":"m"},
                {"type":"book","recv_ms":100,"market_id":"m"},
                {"type":"decision","recv_ms":100,"market_id":"m"}]
    constraints=[{"type":"constraint","recv_ms":100,"market_id":"m","min_order_size":5}]
    result=list(merge_constraints(normalized,constraints))
    assert [row["type"] for row in result] == ["dataset","market","constraint","book","decision"]
    assert normalized[0] == {"type":"dataset","recv_ms":0}


def test_market_map_rejects_side_identity_drift():
    m=markets()["m"]
    with pytest.raises(ValueError,match="identity"):
        market_maps([dict(m,type="market"),dict(m,type="market",up_token_id="x")])
