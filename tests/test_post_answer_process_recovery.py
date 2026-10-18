"""Real process death after durable admission, using the default lease duration."""

import multiprocessing
from pathlib import Path
from threading import Event

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.memory_store import NeuroMemConversationStore
from sage_faculty_twin.post_answer_runtime import PostAnswerRuntime
from test_atomic_turn_commit import runtime_for
from test_post_answer_delivery import delivery_turn


def _leased_worker(directory, ready):
    _, memory, record, task = delivery_turn(Path(directory))
    task = task.model_copy(update={"skip_profile": False})

    def block_after_claim(_record):
        ready.send((record.to_dict(), task.model_dump(mode="json")))
        Event().wait()  # Parent kills this process, not a production worker.

    runtime = PostAnswerRuntime(state=memory.state_store,
                               consolidate_profiles=block_after_claim,
                               score=lambda task: (None, None))
    runtime.submit_turn(task, memory)
    Event().wait()


def test_killed_leased_worker_recovers_without_regenerating_answer(tmp_path):
    from sage_faculty_twin.post_answer_contract import PostAnswerTask

    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    worker = context.Process(target=_leased_worker, args=(str(tmp_path), send))
    worker.start()
    send.close()
    try:
        assert receive.poll(30), "isolated worker did not acquire its task"
        record, payload = receive.recv()
        task = PostAnswerTask.model_validate(payload)
        assert worker.is_alive()
        worker.kill()
        worker.join(10)
        assert worker.exitcode is not None and worker.exitcode != 0

        memory = NeuroMemConversationStore(AppSettings(
            conversation_memory_dir=tmp_path / "memory",
            conversation_memory_index_type="segment"))
        runtime = runtime_for(memory)
        try:
            before = runtime.journal.read(task.request_id)["tasks"]["post_answer"]
            assert before["status"] == "running" and before["attempt"] == 1
            initial = runtime.events.read(task.delivery.stream_id)
            assert not initial["closed"]
            assert [event["payload"]["type"] for event in initial["events"]] == ["answer_done"]
            runtime.start()
            runtime.wait(task.request_id, timeout=90)
            after = runtime.journal.read(task.request_id)["tasks"]["post_answer"]
            assert after["status"] == "done" and after["attempt"] == 2
            records = memory.list_conversation_records("conversation", owner_id="account:alice")
            assert [item.to_dict() for item in records] == [record]
            page = runtime.events.read(task.delivery.stream_id)
            kinds = [event["payload"]["type"] for event in page["events"]]
            assert page["closed"] and kinds[-1] == "complete"
            assert kinds.count("answer_done") == kinds.count("complete") == 1
            assert page["events"][0] == initial["events"][0]
            runtime.submit_turn(task, memory)
            assert runtime.events.read(task.delivery.stream_id) == page
        finally:
            runtime.close()
            memory.state_store.close()
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join(10)
        receive.close()
