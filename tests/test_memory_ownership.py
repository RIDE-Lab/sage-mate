from dataclasses import replace

import pytest

from sage_faculty_twin.chat_identity import ChatIdentity
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.memory_store import ConversationMemoryRecord, NeuroMemConversationStore
from sage_faculty_twin.models import ChatRequest


def make_store(tmp_path):
    return NeuroMemConversationStore(AppSettings(conversation_memory_dir=tmp_path / "memory",
                                                conversation_memory_index_type="segment"))


def request(owner):
    return ChatRequest(owner_id=owner, student_name="Same Name", student_email="same@example.com",
        conversation_id="same-conversation", question="记住我的预约偏好：周三下午讨论研究计划。",
        attachments=[{"file_name": "research.txt", "media_type": "text/plain",
                      "text_content": "Research plan private evidence about retrieval and indexing."}])


def persist(store, owner):
    return store.add_exchange(request(owner), conversation_id="same-conversation",
        answer=f"private answer for {owner}", workflow_action="answer", interaction_domain="research",
        knowledge_hit_count=0, booking_result=None)


def test_same_names_emails_and_conversation_ids_never_cross_owner(tmp_path):
    store = make_store(tmp_path)
    records = {owner: persist(store, owner) for owner in ("account:a", "account:b", None)}
    for owner, own in records.items():
        assert [r.memory_id for r in store.list_conversation_records("same-conversation", owner_id=owner)] == [own.memory_id]
        assert [r.memory_id for r in store.list_recent_conversation_records("same-conversation", owner_id=owner)] == [own.memory_id]
        probe = request(owner).model_copy(update={"question": "research retrieval indexing"})
        hits = store.search(probe, conversation_id="same-conversation", include_long_term=False)
        assert hits and {h.memory_id for h in hits} == {own.memory_id}
        artifacts = store.search_artifacts(probe, conversation_id="same-conversation")
        assert artifacts and all(h.memory_id.startswith(own.memory_id + ":") for h in artifacts)
        for other_owner, other in records.items():
            assert store._should_include_record(other, probe, "same-conversation") == (owner == other_owner)
    # Opening a fresh adapter must preserve scopes and leave legacy unassigned.
    reopened = make_store(tmp_path)
    for owner, own in records.items():
        assert reopened.list_conversation_records("same-conversation", owner_id=owner)[0].to_dict() == own.to_dict()


def test_owned_profiles_are_stable_and_collision_free(tmp_path):
    store = make_store(tmp_path)
    owners = ("account:a_b", "account:a-b", "guest:a_b", None)
    ids = {}
    for owner in owners:
        record = persist(store, owner)
        store.consolidate_profiles(record)
        profiles = store.list_profiles_for_student(student_name="Same Name", student_email="same@example.com", owner_id=owner)
        assert profiles
        assert all(p.owner_id == owner for p in profiles)
        ids[owner] = {p.profile_id for p in profiles}
        if owner is not None:
            changed = replace(record, student_name="Renamed", student_email="changed@example.com")
            assert {p.profile_id for p in store._derive_profiles(changed)} == ids[owner]
            assert all(p.student_key == owner for p in profiles)
    for owner in owners:
        assert all(ids[owner].isdisjoint(ids[other]) for other in owners if other != owner)
        probe = request(owner).model_copy(update={"question": "我的名字和联系方式"})
        hits = store._search_long_term(probe, limit=20)
        assert hits and {h.memory_id for h in hits} <= ids[owner]
    reopened = make_store(tmp_path)
    for owner in owners:
        assert {p.profile_id for p in reopened.list_profiles_for_student(
            student_name="Same Name", student_email="same@example.com", owner_id=owner)} == ids[owner]


def test_legacy_roundtrip_is_not_implicitly_claimed_and_owner_is_validated(tmp_path):
    record = persist(make_store(tmp_path), None)
    legacy = record.to_dict()
    assert "owner_id" not in legacy
    assert ConversationMemoryRecord.from_dict(legacy).to_dict() == legacy
    for invalid in ("", "account:", "owner:a", "account:a/b", 42):
        with pytest.raises(ValueError, match="invalid_memory_owner_id"):
            ConversationMemoryRecord.from_dict({**legacy, "owner_id": invalid})


def test_owned_lookup_reads_canonical_record_without_local_publication(tmp_path):
    stale = make_store(tmp_path)
    writer = make_store(tmp_path)
    source = persist(writer, "account:owner")
    assert stale.get_record(source.memory_id) is None
    assert stale.get_owned_record(source.memory_id, owner_id="account:owner").to_dict() == source.to_dict()
    assert stale.get_owned_record(source.memory_id, owner_id="account:other") is None
    assert stale.get_owned_record(source.memory_id, owner_id=None) is None


def test_client_owner_claim_is_overwritten_by_trusted_identity():
    probe = request("account:victim")
    bound = ChatIdentity("anonymous", "trusted-guest", "general_visitor").bind(probe)
    assert bound.owner_id == "guest:trusted-guest"


def test_index_results_cannot_bypass_canonical_owner_check(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    victim = persist(store, "account:victim")
    store.consolidate_profiles(victim)
    entries = [store._build_conversation_memory_entry(victim),
               *store._build_attachment_memory_entries(victim),
               *(store._build_profile_memory_entry(p) for p in store._profiles.values())]
    # Simulate an index ignoring every filter. Authorization must still consult
    # canonical records, not trust ANN result metadata or retrieval scores.
    monkeypatch.setattr(store, "_retrieve_from_collection", lambda *args: [
        {"id": entry.entry_id, "text": entry.text, "score": 100.0, "metadata": entry.metadata}
        for entry in entries])
    for owner in ("account:attacker", None):
        probe = request(owner).model_copy(update={"question": "research retrieval 我的名字和联系方式"})
        assert store.search(probe, conversation_id=victim.conversation_id) == []
        assert store.search_artifacts(probe, conversation_id=victim.conversation_id) == []


@pytest.mark.parametrize("conflict", ["record_identity", "profile_owner"])
def test_wrong_owner_cannot_be_substituted_in_saved_task(tmp_path, conflict):
    from sage_faculty_twin.models import AvailabilitySchedule, ChatResponse
    from sage_faculty_twin.post_answer_contract import PostAnswerTask

    store = make_store(tmp_path)
    record = persist(store, "account:a")
    with pytest.raises(ValueError, match=f"post_answer_{conflict}_conflict"):
        PostAnswerTask.capture(request_id=record.memory_id,
            request=request("account:b" if conflict == "record_identity" else "account:a"), record=record,
            profile_snapshot=(store._derive_profiles(replace(record, owner_id="account:b"))
                              if conflict == "profile_owner" else ()),
            response=ChatResponse(answer=record.answer, owner_name="Owner", used_model="test",
                conversation_id=record.conversation_id, exchange_id=record.memory_id, memory_write_back=True),
            availability=AvailabilitySchedule(), evaluate_memory=False)
