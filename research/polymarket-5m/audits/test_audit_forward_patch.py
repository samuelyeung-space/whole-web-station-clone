import pytest
from audit_forward_patch import changed_lines, compare_patch, rows_by_key, metrics, SCHEMAS

ROW = "btc-updown-5m-1792650000,1792650000.0,1792650070,2026-10-22,Up,3.0,0.5,1.0,0.5,0.0175,6,True,0.4825,filled,btc,_temp"

def test_truncated_hunk_is_rejected():
    with pytest.raises(ValueError, match="incomplete"):
        changed_lines("@@ -1,2 +1,2 @@\n a\n-b\n")

def test_numeric_rewrite_is_not_a_new_trial():
    newer = ROW.replace(",3.0,", ",3.0000000000000004,")
    result = compare_patch("@@ -2 +2 @@\n-"+ROW+"\n+"+newer, "momentum5s.csv")
    assert result["numeric_only_rewrites"] == 1
    assert result["new_rows"] == [] and result["material_rewrites"] == []

def test_changed_fill_is_material_not_serialization():
    newer = ROW.replace(",True,", ",False,")
    result = compare_patch("@@ -2 +2 @@\n-"+ROW+"\n+"+newer, "momentum5s.csv")
    assert result["material_rewrites"][0]["fields"] == ["filled"]
    assert result["new_identities"] == 0

def test_duplicate_keys_are_rejected():
    header, keys = SCHEMAS["momentum5s.csv"]
    with pytest.raises(ValueError, match="duplicate"):
        rows_by_key([ROW, ROW], header, keys)

def test_appended_row_does_not_consume_context_as_new():
    patch = "@@ -1 +1,2 @@\n header\n+"+ROW
    added, removed = changed_lines(patch)
    assert added == [ROW] and not removed

def test_inconsistent_published_pnl_is_rejected():
    header, keys = SCHEMAS['momentum5s.csv']
    rows = list(rows_by_key([ROW.replace('0.4825', '0.9')], header, keys).values())
    with pytest.raises(ValueError, match='arithmetic'):
        metrics(rows, 'momentum5s.csv')
