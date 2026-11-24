"""Phase 11 branch 6 (brief D13): `manage.py voice_wake_eval record
--from-report` -- a `friday-voice wake-eval` report becomes exactly one
`VoiceWakeEvaluation` row, and the policy endpoint honours it.

`fixtures/wake_eval_report.json` is byte-identical to
`node/friday-voice/fixtures/wake_eval_report.json`, rendered by the
harness's own report builder (`node/friday-voice/tests/test_wake_eval.py`
checks both copies): 24 one-hour household files with 3 false accepts
(FAR 0.125 / h) and 50 positives with 4 false rejects (FRR 0.08), for the
fixture model `f * 64` at the child's threshold (0.5) and refractory period
(2 000 ms).

A tampered report (any number edited, hash mismatch) and an internally
inconsistent one (hours vs. files, FAR vs. false accepts / hours, FRR vs.
false rejects / positives, a duplicate recording, a file name smuggled in)
are refused and write nothing."""

import copy
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from accounts import wake_eval_report
from accounts.models import VoiceWakeEvaluation
from accounts.tests.node_voice_helpers import (
    POLICY_URL,
    FakeRedis,
    bearer,
    make_owner,
    mint_token,
    pair_node,
)
from audit.models import AuditLog
from core import revocation

pytestmark = pytest.mark.django_db

FIXTURE = Path(__file__).parent / "fixtures" / "wake_eval_report.json"
MODEL = "f" * 64
KIND = VoiceWakeEvaluation.Kind
QUERY = {"model_sha256": MODEL, "threshold": "0.5000", "refractory_ms": 2000}


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr(revocation, "_client", lambda: FakeRedis())


@pytest.fixture
def owner():
    return make_owner()


@pytest.fixture
def enabled(settings):
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = True
    settings.VOICE_LIVE_MAX_FAR_PER_HOUR = "0.2"
    settings.VOICE_LIVE_MAX_FRR = "0.10"
    return settings


def report():
    return json.loads(FIXTURE.read_text("utf-8"))


def rehash(document):
    """Recompute every hash, as a careful forger would."""
    for name in ("negative", "positives"):
        if name in document:
            document[name]["manifest_sha256"] = hashlib.sha256(
                wake_eval_report.canonical(document[name]["files"])
            ).hexdigest()
    body = {k: v for k, v in document.items() if k != "report_sha256"}
    document["report_sha256"] = hashlib.sha256(wake_eval_report.canonical(body)).hexdigest()
    return document


def write(tmp_path, document, name="report.json"):
    path = tmp_path / name
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return str(path)


def record(path, kind, *extra):
    call_command("voice_wake_eval", "record", "--from-report", path, "--kind", kind, *extra)


def nothing_written():
    assert VoiceWakeEvaluation.objects.count() == 0
    assert not AuditLog.objects.filter(action="accounts.voice_wake_eval.recorded").exists()


# --- the round trip ---------------------------------------------------------------


def test_a_valid_report_writes_exactly_one_row(owner):
    record(str(FIXTURE), "household")
    row = VoiceWakeEvaluation.objects.get()
    assert (row.kind, row.model_sha256) == (KIND.HOUSEHOLD, MODEL)
    assert row.hours == Decimal("24.000") and row.false_accepts == 3
    assert row.far_per_hour == Decimal("0.1250")
    assert row.threshold == Decimal("0.5000") and row.refractory_ms == 2000
    assert row.report_sha256 == report()["report_sha256"]
    assert row.recorded_by == owner and row.positives == 0 and row.frr is None
    audit = AuditLog.objects.get(action="accounts.voice_wake_eval.recorded")
    assert audit.metadata["kind"] == "household" and audit.metadata["far_per_hour"] == "0.1250"


def test_the_positives_section_writes_one_positives_row(owner):
    record(str(FIXTURE), "positives", "--model-sha256", MODEL)
    row = VoiceWakeEvaluation.objects.get()
    assert (row.kind, row.positives, row.false_rejects) == (KIND.POSITIVES, 50, 4)
    assert row.frr == Decimal("0.0800") and row.hours == 0 and row.far_per_hour is None


def test_the_negative_section_can_be_recorded_as_a_negative_set(owner):
    record(str(FIXTURE), "negative_set")
    assert VoiceWakeEvaluation.objects.get().kind == KIND.NEGATIVE_SET


def test_the_recorded_report_is_honoured_by_the_policy_endpoint(owner, enabled):
    voice = bearer(mint_token(pair_node(owner)[1]))
    assert voice.get(POLICY_URL, QUERY).json()["always_on_permitted"] is False
    record(str(FIXTURE), "household")
    record(str(FIXTURE), "positives")
    body = voice.get(POLICY_URL, QUERY).json()
    assert body["always_on_permitted"] is True and body["reasons"] == []
    household = VoiceWakeEvaluation.objects.get(kind=KIND.HOUSEHOLD)
    assert str(body["household_eval_id"]) == str(household.pk)
    # Measured at 0.5: a child running at another threshold stays refused.
    other = voice.get(POLICY_URL, QUERY | {"threshold": "0.4500"}).json()
    assert other["always_on_permitted"] is False and other["reasons"] == ["threshold_mismatch"]


def test_a_failing_report_closes_the_gate(owner, enabled, tmp_path):
    voice = bearer(mint_token(pair_node(owner)[1]))
    record(str(FIXTURE), "household")
    record(str(FIXTURE), "positives")
    worse = report()
    for entry in worse["negative"]["files"][:3]:
        entry["detections"] += 1  # 6 false accepts in 24 h: 0.25 / h
    worse["negative"]["false_accepts"] = 6
    worse["negative"]["far_per_hour"] = "0.2500"
    record(write(tmp_path, rehash(worse)), "household")
    body = voice.get(POLICY_URL, QUERY).json()
    assert body["always_on_permitted"] is False and body["reasons"] == ["far_above_budget"]


# --- tampered and inconsistent reports ----------------------------------------------


def _edit_far(doc):
    doc["negative"]["far_per_hour"] = "0.0100"


def _edit_hours(doc):
    doc["negative"]["hours"] = "48.000"


def _edit_accepts(doc):
    doc["negative"]["false_accepts"] = 0


def _edit_file(doc):
    doc["negative"]["files"][0]["detections"] += 1


def _edit_model(doc):
    doc["model_sha256"] = "e" * 64


@pytest.mark.parametrize("edit", [_edit_far, _edit_hours, _edit_accepts, _edit_file, _edit_model])
def test_a_number_edited_by_hand_is_a_hash_mismatch(owner, tmp_path, edit):
    document = report()
    edit(document)
    with pytest.raises(CommandError, match="report_sha256 does not match"):
        record(write(tmp_path, document), "household")
    nothing_written()


def test_a_file_list_edited_under_its_manifest_is_refused(owner, tmp_path):
    document = report()
    document["negative"]["files"][0]["ms"] += 1
    body = {k: v for k, v in document.items() if k != "report_sha256"}
    document["report_sha256"] = hashlib.sha256(wake_eval_report.canonical(body)).hexdigest()
    with pytest.raises(CommandError, match="manifest_sha256"):
        record(write(tmp_path, document), "household")
    nothing_written()


def _hours_not_files(doc):
    doc["negative"]["hours"] = "25.000"
    doc["negative"]["far_per_hour"] = "0.1200"


def _far_not_ratio(doc):
    doc["negative"]["far_per_hour"] = "0.0100"


def _accepts_not_files(doc):
    doc["negative"]["false_accepts"] = 0
    doc["negative"]["far_per_hour"] = "0.0000"


def _frr_not_ratio(doc):
    doc["positives"]["frr"] = "0.0100"


def _positives_not_files(doc):
    doc["positives"]["positives"] = 60
    doc["positives"]["frr"] = "0.0667"


def _rejects_not_files(doc):
    doc["positives"]["false_rejects"] = 0
    doc["positives"]["frr"] = "0.0000"


def _duplicate_recording(doc):
    doc["negative"]["files"][1]["sha256"] = doc["negative"]["files"][0]["sha256"]


def _a_file_name(doc):
    doc["negative"]["files"][0]["name"] = "kitchen-monday.wav"


def _a_transcript(doc):
    doc["transcript"] = "hey friday terminali aç"


def _unsorted_files(doc):
    doc["negative"]["files"].reverse()


def _wrong_schema(doc):
    doc["schema"] = "friday-voice/wake-eval/v0"


def _float_threshold(doc):
    doc["threshold"] = 0.5


def _bad_threshold(doc):
    doc["threshold"] = "1.5000"


def _bool_count(doc):
    doc["positives"]["false_rejects"] = True


@pytest.mark.parametrize(
    "edit",
    [
        _hours_not_files,
        _far_not_ratio,
        _accepts_not_files,
        _frr_not_ratio,
        _positives_not_files,
        _rejects_not_files,
        _duplicate_recording,
        _a_file_name,
        _a_transcript,
        _unsorted_files,
        _wrong_schema,
        _float_threshold,
        _bad_threshold,
        _bool_count,
    ],
)
def test_an_inconsistent_report_is_refused_even_rehashed(owner, tmp_path, edit):
    document = report()
    edit(document)
    rehash(document)
    for kind in ("household", "positives"):
        with pytest.raises(CommandError, match="refused"):
            record(write(tmp_path, document), kind)
    nothing_written()


def test_another_model_than_named_is_refused(owner):
    with pytest.raises(CommandError, match="another model"):
        record(str(FIXTURE), "household", "--model-sha256", "e" * 64)
    nothing_written()


def test_a_kind_without_its_section_is_refused(owner, tmp_path):
    document = report()
    del document["positives"]
    path = write(tmp_path, rehash(document))
    with pytest.raises(CommandError, match="positives section"):
        record(path, "positives")
    record(path, "household")  # the negative section alone is fine
    assert VoiceWakeEvaluation.objects.count() == 1


@pytest.mark.parametrize(
    "extra",
    [
        ["--hours", "24"],
        ["--threshold", "0.5"],
        ["--false-accepts", "0"],
        ["--report-sha256", "a" * 64],
    ],
)
def test_explicit_numbers_cannot_be_mixed_with_a_report(owner, extra):
    with pytest.raises(CommandError, match="no explicit numbers"):
        record(str(FIXTURE), "household", *extra)
    nothing_written()


def test_an_unreadable_or_non_json_report_is_refused(owner, tmp_path):
    with pytest.raises(CommandError, match="cannot read"):
        record(str(tmp_path / "missing.json"), "household")
    garbage = tmp_path / "garbage.json"
    garbage.write_bytes(b"\xff\xfe not json")
    with pytest.raises(CommandError, match="not JSON"):
        record(str(garbage), "household")
    (tmp_path / "list.json").write_text("[]")
    with pytest.raises(CommandError, match="JSON object"):
        record(str(tmp_path / "list.json"), "household")
    nothing_written()


def test_without_a_report_the_explicit_fields_are_still_required(owner):
    with pytest.raises(CommandError, match="--threshold, --refractory-ms required"):
        call_command(
            "voice_wake_eval", "record", "--model-sha256", MODEL, "--kind", "positives",
            "--positives", "10",
        )  # fmt: skip
    with pytest.raises(CommandError, match="--model-sha256"):
        call_command(
            "voice_wake_eval", "record", "--kind", "positives", "--threshold", "0.5",
            "--refractory-ms", "2000", "--positives", "10",
        )  # fmt: skip
    nothing_written()


def test_the_validator_is_pure_and_returns_the_row_fields():
    fields = wake_eval_report.evaluation_fields(report(), "household")
    assert fields == {
        "model_sha256": MODEL,
        "kind": "household",
        "threshold": "0.5000",
        "refractory_ms": 2000,
        "report_sha256": report()["report_sha256"],
        "hours": "24.000",
        "false_accepts": 3,
        "far_per_hour": "0.1250",
    }
    # The input is not mutated.
    original = report()
    wake_eval_report.evaluation_fields(copy.deepcopy(original), "positives")
    assert original == report()
