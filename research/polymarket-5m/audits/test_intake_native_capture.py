import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import zipfile
import pytest
from intake_native_capture import intake


def h(data): return hashlib.sha256(data).hexdigest()


def fixture(root, name="raw/day/binance-futures-trade-strict.jsonl.gz"):
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as out:
        for path, data in [(name, b"native-trade-evidence"), ("unselected-large", b"x" * 2500)]:
            info = tarfile.TarInfo(path); info.size = len(data); out.addfile(info, io.BytesIO(data))
    source = tar.getvalue(); parts = []; transfers = []
    for index, offset in enumerate(range(0, len(source), 7000)):
        payload = source[offset:offset+7000]
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z: z.writestr("payload.bin", payload)
        part_name = "part-%05d.zip" % index
        outer_path = root / ("outer-%d.zip" % index)
        with zipfile.ZipFile(outer_path, "w") as z: z.writestr(part_name, inner.getvalue())
        parts.append(dict(index=index, name=part_name, payload_bytes=len(payload), payload_sha256=h(payload),
                          zip_bytes=len(inner.getvalue()), zip_sha256=h(inner.getvalue())))
        transfers.append(dict(index=index, artifact_id=index, local_path=str(outer_path),
                              outer_bytes=outer_path.stat().st_size, outer_sha256=h(outer_path.read_bytes())))
    return dict(parts=parts, source_bytes=len(source), source_sha256=h(source)), transfers


def copy(transfer, target): shutil.copyfile(transfer["local_path"], target)


def test_full_hash_across_parts_and_selected_original_bytes(tmp_path):
    manifest, transfers = fixture(tmp_path)
    r = intake(manifest, transfers, tmp_path/"result", copy)
    assert r["full_capture_archive_verified"] and len(r["parts"]) == 2
    assert len(r["selected_members"]) == 1 and r["financial_result"] is None
    assert (tmp_path/"result/extract/raw/day/binance-futures-trade-strict.jsonl.gz").read_bytes() == b"native-trade-evidence"
    assert not list((tmp_path/"result").glob("verified-transfer*"))
    assert all(Path(t["local_path"]).exists() for t in transfers)


@pytest.mark.parametrize("layer", ["outer", "inner", "payload", "source"])
def test_any_identity_layer_corruption_fails_closed(tmp_path, layer):
    manifest, transfers = fixture(tmp_path)
    if layer == "source": manifest["source_sha256"] = "0"*64
    elif layer == "outer": transfers[0]["outer_sha256"] = "0"*64
    else: manifest["parts"][0]["zip_sha256" if layer == "inner" else "payload_sha256"] = "0"*64
    with pytest.raises(ValueError, match="mismatch"):
        intake(manifest, transfers, tmp_path/"result", copy)
    assert not (tmp_path/"result/receipt.json").exists()


def test_path_traversal_selected_member_is_rejected(tmp_path):
    manifest, transfers = fixture(tmp_path, "../capture-health.json")
    with pytest.raises(ValueError, match="unsafe"):
        intake(manifest, transfers, tmp_path/"result", copy)


def test_missing_part_is_rejected(tmp_path):
    manifest, transfers = fixture(tmp_path)
    with pytest.raises(ValueError, match="incomplete"):
        intake(manifest, transfers[:-1], tmp_path/"result", copy)


def test_transfer_failure_does_not_echo_short_lived_url(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import intake_native_capture as module
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=22))
    with pytest.raises(RuntimeError) as error:
        module.download({"download_url": "https://example.test/short-lived-private-reference"}, tmp_path/"unused")
    assert "private-reference" not in str(error.value)
