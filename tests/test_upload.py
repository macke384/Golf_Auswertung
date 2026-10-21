"""Upload-Streaming: Limits, Prüfsummen, Abbrüche und Aufräumen."""
import hashlib
import os
import time

import anyio
import pytest

from app.config import Settings
from app.imports import upload
from app.imports.upload import UploadError


@pytest.fixture
def settings(tmp_path):
    return Settings(env="test", storage_dir=tmp_path / "storage", upload_max_bytes=10_000)


async def gen(*chunks, fail_after=None):
    for i, c in enumerate(chunks):
        if fail_after is not None and i == fail_after:
            raise ConnectionResetError("client weg")
        yield c


def tmp_files(settings):
    return list(settings.tmp_dir.glob("*")) if settings.tmp_dir.exists() else []


def run(coro_fn, *a, **kw):
    return anyio.run(lambda: coro_fn(*a, **kw))


def test_stream_ok_hash_and_kind(settings):
    data = b"Name;1;2\nA;4;5\n"
    st = run(upload.receive_stream, gen(data[:5], data[5:]), settings)
    assert st.sha256 == hashlib.sha256(data).hexdigest()
    assert st.kind == "csv" and st.size == len(data)
    final = upload.commit(st, settings)
    assert final.read_bytes() == data and not tmp_files(settings)


def test_size_limit_removes_temp(settings):
    with pytest.raises(UploadError) as e:
        run(upload.receive_stream, gen(b"a" * 6000, b"a" * 6000), settings)
    assert e.value.status == 413
    assert not tmp_files(settings)


def test_declared_length_too_big_rejected_before_reading(settings):
    with pytest.raises(UploadError) as e:
        run(upload.receive_stream, gen(b"x"), settings, declared_length=10**9)
    assert e.value.status == 413


def test_truncated_upload_detected(settings):
    with pytest.raises(UploadError):
        run(upload.receive_stream, gen(b"abc"), settings, declared_length=10)
    assert not tmp_files(settings)


def test_client_abort_removes_temp(settings):
    with pytest.raises(ConnectionResetError):
        run(upload.receive_stream, gen(b"abc", b"def", fail_after=1), settings)
    assert not tmp_files(settings)


def test_checksum_mismatch(settings):
    with pytest.raises(UploadError) as e:
        run(upload.receive_stream, gen(b"Name;1\n"), settings, expected_sha256="0" * 64)
    assert e.value.status == 400 and not tmp_files(settings)


def test_empty_and_binary_rejected(settings):
    with pytest.raises(UploadError):
        run(upload.receive_stream, gen(b""), settings)
    with pytest.raises(UploadError) as e:
        run(upload.receive_stream, gen(b"MZ\x00\x00\x90binary"), settings)
    assert e.value.status == 415
    assert not tmp_files(settings)


def test_magic_bytes_win_over_client_metadata(settings):
    st = run(upload.receive_stream, gen(b"%PDF-1.4 ..."), settings)
    assert st.kind == "pdf"
    upload.discard(st)


def test_utf8_multibyte_split_at_head_boundary():
    head = ("a" * 4095 + "ü").encode()[:4096]  # ü abgeschnitten
    assert upload.detect_kind(head) == "csv"


def test_sweep_removes_only_old_unreferenced(settings):
    st = run(upload.receive_stream, gen(b"Name;1\nA;4\n"), settings)
    kept = upload.commit(st, settings)
    st2 = run(upload.receive_stream, gen(b"Name;1\nB;5\n"), settings)
    orphan = upload.commit(st2, settings)
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    stale_tmp = settings.tmp_dir / "up-stale"
    stale_tmp.write_bytes(b"x")
    fresh_tmp = settings.tmp_dir / "up-fresh"
    fresh_tmp.write_bytes(b"x")
    old = time.time() - 2 * settings.orphan_grace_seconds
    for p in (kept, orphan, stale_tmp):
        os.utime(p, (old, old))
    removed = upload.sweep_orphans(settings, {st.sha256})
    assert removed == 2
    assert kept.exists() and fresh_tmp.exists()
    assert not orphan.exists() and not stale_tmp.exists()
