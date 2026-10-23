"""Compressed transport fixtures only; no market-profit evidence."""
import hashlib
import io
import shutil
import tarfile
import zipfile
import pytest
from intake_converted_capture import intake


def sha(data): return hashlib.sha256(data).hexdigest()


def fixture(root, extra=None, omit=None):
    members = [("bundle-btc/strict/"+name, (name+" evidence").encode()) for name in
               ("manifest.json", "source_events.jsonl.gz", "clob_events.jsonl.gz",
                "market_registry.csv.gz", "market_outcomes.csv.gz") if name != omit]
    members += [("bundle-eth/private", b"unselected")]
    if extra: members.append(extra)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members:
            item = tarfile.TarInfo(name); item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
    source = buf.getvalue(); parts = []; transfers = []
    width = max(1, len(source)//3)
    for index, offset in enumerate(range(0, len(source), width)):
        payload = source[offset:offset+width]; inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z: z.writestr("payload.bin", payload)
        name = "part-%05d.zip" % index; path = root/("outer%d.zip" % index)
        with zipfile.ZipFile(path, "w") as z: z.writestr(name, inner.getvalue())
        parts.append(dict(index=index, name=name, payload_bytes=len(payload), payload_sha256=sha(payload),
                          zip_bytes=len(inner.getvalue()), zip_sha256=sha(inner.getvalue())))
        transfers.append(dict(index=index, artifact_id=index, local_path=str(path),
                              outer_bytes=path.stat().st_size, outer_sha256=sha(path.read_bytes())))
    return dict(source_name="raw.tgz", source_bytes=len(source), source_sha256=sha(source), parts=parts), transfers


def copy(transfer, target): shutil.copyfile(transfer["local_path"], target)


def test_compressed_multilayer_stream_and_selection(tmp_path):
    manifest, transfers = fixture(tmp_path)
    result = intake(manifest, transfers, tmp_path/"result", copy)
    assert result["full_converted_archive_verified"] and len(result["parts"]) > 1
    assert len(result["selected_members"]) == 5 and result["financial_result"] is None
    assert not (tmp_path/"result/extract/bundle-eth").exists()
    assert (tmp_path/"result/extract/bundle-btc/strict/manifest.json").read_bytes() == b"manifest.json evidence"


@pytest.mark.parametrize("layer", ["outer", "inner", "payload", "source"])
def test_corruption_never_admits_source(tmp_path, layer):
    manifest, transfers = fixture(tmp_path)
    if layer == "source": manifest["source_sha256"] = "0"*64
    elif layer == "outer": transfers[0]["outer_sha256"] = "0"*64
    else: manifest["parts"][0]["zip_sha256" if layer == "inner" else "payload_sha256"] = "0"*64
    with pytest.raises(ValueError, match="mismatch"):
        intake(manifest, transfers, tmp_path/"result", copy)
    assert not (tmp_path/"result/receipt.json").exists()


@pytest.mark.parametrize("extra,omit,match", [
    (("bundle-btc/../../escape", b"bad"), None, "unsafe"),
    (("bundle-btc/strict/manifest.json", b"duplicate"), None, "duplicate"),
    (None, "market_outcomes.csv.gz", "incomplete"),
])
def test_invalid_selected_bundle_is_rejected(tmp_path, extra, omit, match):
    manifest, transfers = fixture(tmp_path, extra, omit)
    with pytest.raises(ValueError, match=match):
        intake(manifest, transfers, tmp_path/"result", copy)
    assert not (tmp_path/"result/receipt.json").exists()
