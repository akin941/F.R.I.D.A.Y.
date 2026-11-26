"""Phase 13 D6: `nodes.targeting.resolve_target`, one test per rule with two
and three nodes (`nodes.tests.two_nodes`): explicit name / alias / room /
role / platform, explicit offline or lacking -> refused naming it (never
another node), the model's node only checked (`target_mismatch`,
`node_unknown`), the speaking node, last active within / outside the window
and ties, ask, no candidate, revoked / killed nodes, isolation between
owners, and no model call."""

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import NodeKillSwitch
from accounts.services import register_user
from nodes import bus, targeting
from nodes.activity import mark_node_active
from nodes.models import Node
from nodes.tests.two_nodes import add_node, cleanup, make_two_nodes
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

READ = {"claude.sessions.list", "codex.threads.list"}
ACTION = {"repo.command.run"}


@pytest.fixture
def world(owner, personal):
    world = make_two_nodes(owner, personal)
    yield world
    cleanup(world)


@pytest.fixture
def three(owner, personal):
    world = make_two_nodes(owner, personal, third=True)
    yield world
    cleanup(world)


def resolve(world, text="", capabilities=READ, **kwargs):
    return targeting.resolve_target(
        world.owner, capabilities=capabilities, source_text=text, **kwargs
    )


def resolved(target, node, reason=targeting.REASON_MENTION):
    assert isinstance(target, targeting.Resolved), target
    assert (target.node.pk, target.reason) == (node.pk, reason)


def ambiguous(target, *nodes):
    assert isinstance(target, targeting.Ambiguous), target
    assert {n.pk for n in target.candidates} == {n.pk for n in nodes}


def refused(target, code, node=None):
    assert isinstance(target, targeting.Refused), target
    assert target.code == code
    assert (target.node.pk if target.node is not None else None) == (
        node.pk if node is not None else None
    )


def offline(node):
    bus.clear_presence(node.pk)


def active(node, minutes_ago):
    Node.objects.filter(pk=node.pk).update(
        last_active_at=timezone.now() - timedelta(minutes=minutes_ago)
    )


# --- rule 1: explicit -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "which"),
    [
        ("MacBook'taki oturumlarım ne durumda?", "mac"),
        ("macbookta oturumlar", "mac"),
        ("Masaüstündeki Claude oturumları", "desk"),
        ("Masaüstü'nde ne çalışıyor", "desk"),
        ("iş bilgisayarındaki oturumlar", "mac"),  # the alias
        ("Mac'teki oturumlar", "mac"),  # a platform word, one macOS node
    ],
)
def test_a_named_node_wins(world, text, which):
    resolved(resolve(world, text), getattr(world, which))


def test_an_explicit_name_beats_origin_and_last_active(world):
    active(world.mac, 1)
    target = resolve(world, "Masaüstünde oturumlar", origin_node_id=str(world.mac.pk))
    resolved(target, world.desk)


def test_a_named_offline_node_is_refused_naming_it_never_another(world):
    offline(world.desk)
    refused(resolve(world, "Masaüstündeki oturumlar"), targeting.NODE_OFFLINE, world.desk)


def test_a_named_node_lacking_the_capability_is_refused_naming_it(world):
    # Masaüstü is v1: no action capability is invocable there.
    target = resolve(world, "Masaüstünde testleri çalıştır", capabilities=ACTION)
    refused(target, targeting.NODE_LACKS_CAPABILITY, world.desk)


def test_a_named_node_under_the_kill_switch_is_unusable(world):
    NodeKillSwitch.objects.create(user=world.owner, engaged_at=timezone.now())
    refused(resolve(world, "MacBook'ta oturumlar"), targeting.NODE_UNUSABLE, world.mac)
    # Unnamed: no candidate at all.
    refused(resolve(world, "oturumlarım"), targeting.NOT_AVAILABLE)


def test_two_named_nodes_ask(world):
    ambiguous(
        resolve(world, "MacBook ile Masaüstündeki oturumları karşılaştır"), world.mac, world.desk
    )


def test_a_room_with_one_node_resolves_to_it(three):
    resolved(resolve(three, "salondaki bilgisayarda oturumlar"), three.media)


def test_a_room_with_two_online_nodes_asks(three):
    ambiguous(resolve(three, "çalışma odasındaki oturumlar"), three.mac, three.desk)


def test_a_room_with_two_nodes_one_online_narrows_to_it(three):
    offline(three.mac)
    resolved(resolve(three, "çalışma odasındaki oturumlar"), three.desk)


def test_a_room_with_no_node_refuses(world, owner, personal):
    from core.channels import CHANNEL_WEB
    from workspaces import rooms

    rooms.create_room(actor=owner, workspace=personal, channel=CHANNEL_WEB, name="Mutfak")
    # The owner named a room with no PC in it: refused, never another node.
    refused(resolve(world, "mutfaktaki bilgisayarda oturumlar"), targeting.NO_NODE_IN_ROOM)
    refused(resolve(world, "salondaki bilgisayarda oturumlar"), targeting.NO_NODE_IN_ROOM)


def test_a_role_word_narrows(three):
    resolved(resolve(three, "medya bilgisayarındaki oturumlar"), three.media)
    resolved(resolve(three, "dizüstündeki oturumlar"), three.mac)


def test_a_platform_word_with_two_such_nodes_asks(three):
    ambiguous(resolve(three, "Linux'taki oturumlar"), three.desk, three.media)


def test_a_role_word_no_node_has_is_ignored(world):
    active(world.desk, 1)
    # "sunucu" (server): no node has that role -> not a mention.
    resolved(resolve(world, "sunucudaki oturumlar"), world.desk, targeting.REASON_LAST_ACTIVE)


def test_room_and_role_together(three):
    resolved(resolve(three, "çalışma odasındaki dizüstünde oturumlar"), three.mac)


# --- the model's node: checked, never deciding -------------------------------


def test_the_models_node_must_agree_with_the_mention(world):
    target = resolve(world, "Masaüstünde oturumlar", requested_node_id=str(world.mac.pk))
    refused(target, targeting.TARGET_MISMATCH, world.desk)
    target = resolve(world, "Masaüstünde oturumlar", requested_label="MacBook")
    refused(target, targeting.TARGET_MISMATCH, world.desk)


def test_the_models_node_agreeing_resolves(world):
    target = resolve(world, "Masaüstünde oturumlar", requested_node_id=str(world.desk.pk))
    resolved(target, world.desk)


def test_the_models_node_cannot_settle_an_ambiguity(world):
    target = resolve(world, "oturumlarım", requested_node_id=str(world.mac.pk))
    ambiguous(target, world.mac, world.desk)


def test_the_models_node_cannot_override_the_only_candidate(world):
    offline(world.mac)
    target = resolve(world, "oturumlarım", requested_node_id=str(world.mac.pk))
    refused(target, targeting.TARGET_MISMATCH, world.desk)


def _foreign_node(user, name):
    """A node row of another user with the same name (only the installation
    owner pairs through the service)."""
    import secrets

    from accounts.authentication import hash_token
    from accounts.models import TrustedDevice
    from nodes.keys import signing_keyring

    device = TrustedDevice.objects.create(
        user=user,
        name=name,
        kind=TrustedDevice.Kind.NODE,
        secret_hash=hash_token("jnd_" + secrets.token_urlsafe(24)),
        enrolled_via=TrustedDevice.EnrolledVia.values[0],
    )
    node = Node.objects.create(
        id=device.pk,
        device=device,
        owner=user,
        workspace=Workspace.objects.get(owner=user, type=Workspace.Type.PERSONAL),
        name=name,
        name_key=name.lower(),
        protocol_version=1,
        pinned_key_id=signing_keyring().key_id,
    )
    bus.mark_present(node.pk)
    return node


def test_another_owners_node_or_an_unknown_label_is_unknown(world):
    stranger = register_user(email="other@test.dev", password="Str0ngDevPass!42", name="Other")
    theirs = _foreign_node(stranger, "MacBook")
    try:
        target = resolve(world, "oturumlar", requested_node_id=str(theirs.pk))
        refused(target, targeting.NODE_UNKNOWN)
        refused(resolve(world, "oturumlar", requested_label="Kırmızı"), targeting.NODE_UNKNOWN)
        refused(resolve(world, "oturumlar", requested_node_id="not-a-uuid"), targeting.NODE_UNKNOWN)
        # Their node carries the same name: the owner's words resolve to the
        # owner's node only, and theirs is never a candidate.
        resolved(resolve(world, "MacBook'ta oturumlar"), world.mac)
        ambiguous(resolve(world, "oturumlar"), world.mac, world.desk)
        # Nor is the owner's node theirs to reach.
        refused(
            targeting.resolve_target(stranger, capabilities=READ, source_text="MacBook'ta"),
            targeting.NOT_AVAILABLE,
        )
    finally:
        bus.clear_presence(theirs.pk)


# --- rules 2-4 ----------------------------------------------------------------


def test_the_speaking_node_wins_over_last_active(world):
    active(world.desk, 1)
    target = resolve(world, "oturumlarım", origin_node_id=str(world.mac.pk))
    resolved(target, world.mac, targeting.REASON_ORIGIN)


def test_an_offline_speaking_node_falls_to_last_active(world):
    offline(world.mac)
    target = resolve(world, "oturumlarım", origin_node_id=str(world.mac.pk))
    resolved(target, world.desk, targeting.REASON_ONLY_CANDIDATE)


def test_a_speaking_node_lacking_the_capability_falls_through(world):
    target = resolve(
        world, "testleri çalıştır", capabilities=ACTION, origin_node_id=str(world.desk.pk)
    )
    resolved(target, world.mac, targeting.REASON_ONLY_CANDIDATE)


def test_last_active_within_the_window_wins(world, settings):
    settings.NODE_LAST_ACTIVE_WINDOW_SECONDS = 900
    active(world.desk, 5)
    active(world.mac, 30)
    resolved(resolve(world, "oturumlarım"), world.desk, targeting.REASON_LAST_ACTIVE)


def test_last_active_outside_the_window_asks(world, settings):
    settings.NODE_LAST_ACTIVE_WINDOW_SECONDS = 900
    active(world.desk, 16)
    ambiguous(resolve(world, "oturumlarım"), world.mac, world.desk)


def test_a_last_active_tie_asks(world):
    stamp = timezone.now() - timedelta(minutes=2)
    Node.objects.filter(pk__in=[world.mac.pk, world.desk.pk]).update(last_active_at=stamp)
    ambiguous(resolve(world, "oturumlarım"), world.mac, world.desk)


def test_one_online_node_is_the_only_candidate(world):
    offline(world.desk)
    resolved(resolve(world, "oturumlarım"), world.mac, targeting.REASON_ONLY_CANDIDATE)


def test_no_candidate_is_not_available(world):
    offline(world.mac)
    offline(world.desk)
    refused(resolve(world, "oturumlarım"), targeting.NOT_AVAILABLE)


def test_the_owners_only_node_offline_is_named(owner, personal):
    node = add_node(owner, name="MacBook", platform="macos", role="laptop")
    offline(node)
    refused(
        targeting.resolve_target(owner, capabilities=READ, source_text="oturumlar"),
        targeting.NODE_OFFLINE,
        node,
    )


def test_revoked_nodes_are_never_candidates_or_matched(world):
    world.desk.device.revoked_at = timezone.now()
    world.desk.device.save(update_fields=["revoked_at"])
    resolved(resolve(world, "oturumlarım"), world.mac, targeting.REASON_ONLY_CANDIDATE)
    # Its old name no longer names anything: no mention -> the default rules.
    resolved(resolve(world, "Masaüstündeki oturumlar"), world.mac, targeting.REASON_ONLY_CANDIDATE)
    assert [n.pk for n in targeting.eligible_nodes(world.owner, capabilities=READ)] == [
        world.mac.pk
    ]


def test_an_owner_without_nodes_or_no_owner(db, owner):
    refused(targeting.resolve_target(owner, capabilities=READ), targeting.NOT_AVAILABLE)
    refused(targeting.resolve_target(None, capabilities=READ), targeting.NOT_AVAILABLE)


# --- a node-controlled `last_active_at` (review follow-up 2) ------------------


def test_a_node_bumping_last_active_cannot_change_an_explicit_or_origin_resolution(world):
    # A compromised Masaüstü keeps relaying hook events: it is "last active".
    Node.objects.filter(pk=world.desk.pk).update(last_active_at=None)
    assert mark_node_active(world.desk.pk)
    resolved(resolve(world, "MacBook'ta oturumlar"), world.mac)
    target = resolve(world, "oturumlarım", origin_node_id=str(world.mac.pk))
    resolved(target, world.mac, targeting.REASON_ORIGIN)


# --- no model call; replies ---------------------------------------------------


def test_resolution_is_code_only(world):
    """No model call: `nodes` cannot reach `ai_core` at all (import
    contract), so the resolver cannot make one; the turn-level ModelUsage
    counts are pinned in `tools/tests/test_node_targeting_actions.py` and
    `assistant/tests/test_which_pc.py`."""
    import nodes.targeting as module

    assert "ai_core" not in module.__dict__
    for text in ("MacBook'ta", "oturumlar", "çalışma odasındaki", ""):
        resolve(world, text)


def test_the_ask_template_lists_the_candidates_with_vowel_harmony(world):
    target = resolve(world, "oturumlarım")
    text = targeting.reply_text(target)
    assert text.startswith("Hangi bilgisayar: MacBook mu, Masaüstü mü?")
    assert "çalıştırmadım" in text


@pytest.mark.parametrize(
    ("name", "particle"),
    [("MacBook", "mu"), ("Masaüstü", "mü"), ("Kirpi", "mi"), ("Kasa", "mı"), ("Göz", "mü")],
)
def test_question_particle(name, particle):
    assert targeting._question_particle(name) == particle


def test_refusal_texts_name_the_node(world):
    offline(world.desk)
    text = targeting.reply_text(resolve(world, "Masaüstündeki oturumlar"))
    assert text.startswith("Masaüstü şu an çevrimdışı")
    assert targeting.reply_text(targeting.Refused(targeting.TARGET_MISMATCH))
    assert targeting.reply_text(targeting.Resolved(world.mac, "mention")) == ""
    for code in targeting.REFUSAL_CODES:
        assert code in targeting.REFUSAL_TEXTS
