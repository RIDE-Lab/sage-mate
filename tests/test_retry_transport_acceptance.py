"""Real loopback HTTP and SMTP acknowledgement-loss acceptance.

Run in a network namespace with no external interfaces. Real application routes,
SAGE journals and booking persistence; synthetic LLM and SMTP destination only.
No user data, model services or real email addresses are used.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import socketserver
import threading
import time

import httpx
import pytest
import uvicorn

from sage_faculty_twin import api
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.guest_identity import GUEST_COOKIE_NAME
from sage_faculty_twin.notifications import BookingEmailNotifier
from sage_faculty_twin.service import DigitalTwinService
from test_chat_post_answer_background import _FastLLMClient


@contextmanager
def tcp_app(instance, monkeypatch):
    @asynccontextmanager
    async def lifespan(app):
        flow = api._create_http_workflow(instance)
        monkeypatch.setattr(api, "http_workflow", flow)
        await flow.start()
        instance._meeting_service.start_notifications(
            BookingEmailNotifier(instance._settings)
        )
        try:
            yield
        finally:
            await flow.aclose()
            await instance.aclose()

    monkeypatch.setattr(api, "service", instance)
    monkeypatch.setattr(api.llm_app.router, "lifespan_context", lifespan)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(api.llm_app, log_level="error", access_log=False)
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(15)
        assert not thread.is_alive()
        listener.close()


@contextmanager
def drop_proxy(port):
    class Proxy(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            upstream = http.client.HTTPConnection("127.0.0.1", port, timeout=40)
            upstream.request(
                "POST",
                self.path,
                body,
                {
                    "Content-Type": "application/json",
                    "Cookie": self.headers.get("Cookie", ""),
                    **(
                        {"Idempotency-Key": self.headers["Idempotency-Key"]}
                        if self.headers.get("Idempotency-Key")
                        else {}
                    ),
                },
            )
            response = upstream.getresponse()
            data = response.read()
            upstream.close()
            if self.headers.get("X-Drop-Reply") == "1":
                self.close_connection = True
                return
            self.send_response(response.status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextmanager
def smtp_sink():
    accepted = []

    class SMTP(socketserver.StreamRequestHandler):
        def reply(self, value):
            self.wfile.write(value + b"\r\n")
            self.wfile.flush()

        def handle(self):
            self.reply(b"220 isolated.invalid ESMTP")
            while line := self.rfile.readline():
                verb = line.split(None, 1)[0].upper()
                if verb in {b"EHLO", b"HELO", b"MAIL", b"RCPT", b"RSET"}:
                    self.reply(b"250 OK")
                elif verb == b"DATA":
                    self.reply(b"354 end with dot")
                    body = []
                    while (line := self.rfile.readline()) not in {b".\r\n", b""}:
                        body.append(line)
                    accepted.append(b"".join(body))
                    # Real SMTP DATA accepted. Deliberately lose its final 250.
                    return
                elif verb == b"QUIT":
                    self.reply(b"221 bye")
                    return
                else:
                    self.reply(b"500 unsupported")

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SMTP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], accepted
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("streamed", [False, True])
def test_lost_http_reply_concurrent_retry_and_reopen_generate_once(
    tmp_path, monkeypatch, streamed
):
    settings = AppSettings(
        runtime_dir=tmp_path,
        model_name="fixture",
        neuromem_index_type="bm25",
        conversation_memory_index_type="bm25",
    )
    calls = []
    token = None
    response_id = None
    payload = {
        "student_name": "Synthetic",
        "question": "给我一些下一步建议。",
        "conversation_id": "isolated-retry",
    }
    route = "/chat/stream" if streamed else "/chat"

    def answer_json(response):
        response.raise_for_status()
        if not streamed:
            return response.json()
        import json

        frames = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert frames[-1]["type"] == "result", frames
        return frames[-1]["response"]

    for round_ in range(2):
        instance = DigitalTwinService(settings)
        instance._llm_client = _FastLLMClient()
        answer = instance.answer

        async def counted(*args, **kwargs):
            calls.append(1)
            return await answer(*args, **kwargs)

        monkeypatch.setattr(instance, "answer", counted)
        if token is None:
            token, _ = instance._guest_identity_store.issue()
        headers = {
            "Cookie": GUEST_COOKIE_NAME + "=" + token,
            "Idempotency-Key": "stable-turn",
        }
        with tcp_app(instance, monkeypatch) as port, drop_proxy(port) as base:
            if round_ == 0:
                with pytest.raises(httpx.RemoteProtocolError):
                    httpx.post(
                        base + route,
                        json=payload,
                        headers={**headers, "X-Drop-Reply": "1"},
                        timeout=40,
                        trust_env=False,
                    )
            with ThreadPoolExecutor(4) as pool:
                results = list(
                    pool.map(
                        lambda _: answer_json(
                            httpx.post(
                                base + route,
                                json=payload,
                                headers=headers,
                                timeout=40,
                                trust_env=False,
                            )
                        ),
                        range(4),
                    )
                )
            assert len({r["exchange_id"] for r in results}) == 1
            assert results[0]["answer"]
            if response_id:
                assert results[0]["exchange_id"] == response_id
            response_id = results[0]["exchange_id"]
            assert calls == [1]
            if not streamed:
                conflict = httpx.post(
                    base + route,
                    json={**payload, "question": "changed"},
                    headers=headers,
                    trust_env=False,
                )
                assert conflict.status_code == 409
                mismatch = httpx.post(
                    base + route + "?request_id=different",
                    json=payload,
                    headers=headers,
                    trust_env=False,
                )
                assert mismatch.status_code == 409


def test_booking_confirm_ack_loss_and_smtp_acceptance_never_repeat(
    tmp_path, monkeypatch
):
    with smtp_sink() as (smtp_port, accepted):
        settings = AppSettings(
            runtime_dir=tmp_path,
            model_name="fixture",
            neuromem_index_type="bm25",
            conversation_memory_index_type="bm25",
            smtp_host="127.0.0.1",
            smtp_port=smtp_port,
            smtp_use_tls=False,
            smtp_use_ssl=False,
            smtp_sender="sender@example.test",
            booking_notification_email="admin@example.test",
        )
        token = action = booking_id = None
        for round_ in range(2):
            instance = DigitalTwinService(settings)
            if token is None:
                token, _ = instance._guest_identity_store.issue()
            headers = {"Cookie": GUEST_COOKIE_NAME + "=" + token}
            with tcp_app(instance, monkeypatch) as port, drop_proxy(port) as base:
                if round_ == 0:
                    draft = {
                        "student_name": "Synthetic",
                        "student_email": "student@example.test",
                        "topic": "Synthetic",
                        "preferred_start": "2030-01-07T10:00:00",
                        "preferred_end": "2030-01-07T11:00:00",
                    }
                    draft_headers = {**headers, "Idempotency-Key": "stable-draft"}
                    with pytest.raises(httpx.RemoteProtocolError):
                        httpx.post(
                            base + "/bookings/proposals",
                            json=draft,
                            headers={**draft_headers, "X-Drop-Reply": "1"},
                            trust_env=False,
                        )
                    proposal = httpx.post(
                        base + "/bookings/proposals",
                        json=draft,
                        headers=draft_headers,
                        trust_env=False,
                    )
                    conflict = httpx.post(
                        base + "/bookings/proposals",
                        json={**draft, "topic": "changed"},
                        headers=draft_headers,
                        trust_env=False,
                    )
                    assert conflict.status_code == 409
                    assert proposal.status_code == 200, proposal.text
                    action = proposal.json()["proposal"]["action_id"]
                    assert (
                        not instance._meeting_service.list_bookings() and accepted == []
                    )
                    with pytest.raises(httpx.RemoteProtocolError):
                        httpx.post(
                            base + "/bookings/confirm-proposal",
                            json={"action_id": action},
                            headers={**headers, "X-Drop-Reply": "1"},
                            trust_env=False,
                        )
                with ThreadPoolExecutor(4) as pool:
                    results = list(
                        pool.map(
                            lambda _: httpx.post(
                                base + "/bookings/confirm-proposal",
                                json={"action_id": action},
                                headers=headers,
                                trust_env=False,
                            ).json(),
                            range(4),
                        )
                    )
                ids = {r["booking"]["booking_id"] for r in results}
                assert len(ids) == 1
                if booking_id:
                    assert ids == {booking_id}
                booking_id = results[0]["booking"]["booking_id"]
                assert len(instance._meeting_service.list_bookings()) == 1
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    view = httpx.get(
                        f"http://127.0.0.1:{port}/bookings/proposals/{action}",
                        headers=headers,
                        trust_env=False,
                    ).json()
                    if view["notification"]["status"] == "unknown":
                        break
                    time.sleep(0.05)
                assert view["notification"]["status"] == "unknown", view
                assert len(accepted) == 1


@pytest.mark.parametrize("streamed", [False, True])
def test_model_accepted_but_read_times_out_is_not_resent(streamed):
    from test_llm_client import _build_retry_test_client

    accepted = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            accepted.append(self.path)
            time.sleep(
                1.5
            )  # Request accepted, but reply arrives after client deadline.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport = httpx.Client(
        base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False, timeout=0.1
    )
    settings = AppSettings(
        _env_file=None,
        llm_timeout_seconds=1,
        llm_retry_attempts=3,
        llm_cache_ttl_seconds=0,
        llm_cache_max_entries=0,
        llm_retry_backoff_seconds=0,
    )
    client = _build_retry_test_client(settings, transport)
    try:
        with pytest.raises(httpx.ReadTimeout):
            if streamed:
                client._request_chat_completion_stream(
                    {"model": "fixture", "messages": []}, lambda _: None
                )
            else:
                client._request_chat_completion_sync(
                    {"model": "fixture", "messages": []}
                )
        assert len(accepted) == 1
    finally:
        transport.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_follow_up_mail_lost_ack_is_visible_and_not_resent_after_reopen(tmp_path):
    with smtp_sink() as (smtp_port, accepted):
        settings = AppSettings(
            runtime_dir=tmp_path,
            model_name="fixture",
            neuromem_index_type="bm25",
            conversation_memory_index_type="bm25",
            smtp_host="127.0.0.1",
            smtp_port=smtp_port,
            smtp_use_tls=False,
            smtp_use_ssl=False,
            smtp_sender="sender@example.test",
        )
        for round_ in range(2):
            instance = DigitalTwinService(settings)
            try:
                if round_ in (0, 1):
                    instance._follow_up_store.queue_action(
                        booking_id="synthetic-booking",
                        student_name="Synthetic",
                        student_email="student@example.test",
                        action_type="fixture",
                        title="Synthetic",
                        detail="Synthetic",
                        subject="Synthetic",
                        lines=["No real mail"],
                        due_at=None,
                    )
                result = instance.dispatch_due_follow_ups()
                assert result.sent_count == 0
                assert result.unknown_count == (1 if round_ == 0 else 0)
                assert len(instance.list_follow_up_actions(status="unknown")) == 1
                assert len(accepted) == 1
            finally:
                asyncio.run(instance.aclose())


def _crash_answer_worker(path, payload, endpoint):
    from sage.runtime.flownet.runtime.operator_runtime.stateful import (
        StatefulProcessRuntime,
    )
    from sage_faculty_twin.http_workflow import HttpIngress, HttpWorkflow
    from sage_faculty_twin.models import ChatResponse

    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)

        async def authorize(item):
            return True

        async def recover(item):
            return None

        async def answer(item, **kwargs):
            async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
                await client.post(
                    endpoint, json={"synthetic_operation": item.request_id}
                )
            return ChatResponse(
                answer="Synthetic", owner_name="Twin", used_model="fixture"
            )

        flow = HttpWorkflow(
            state,
            authorize=authorize,
            recover=recover,
            answer=answer,
            lease_seconds=0.6,
            poll_interval=0.05,
        )
        item = HttpIngress.model_validate(payload)
        await flow.admit(item)
        await flow.wait(item, timeout=30)

    asyncio.run(scenario())


def test_process_death_after_model_acceptance_does_not_regenerate(tmp_path):
    import multiprocessing
    from sage.runtime.flownet.runtime.operator_runtime.stateful import (
        StatefulProcessRuntime,
    )
    from sage_faculty_twin.http_workflow import HttpWorkflow, HttpWorkRejected
    from test_http_workflow import ingress

    accepted, release = threading.Event(), threading.Event()
    calls = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(1)
            accepted.set()
            release.wait(15)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = str(tmp_path / "crash.db")
    item = ingress()
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_answer_worker,
        args=(
            path,
            item.model_dump(mode="json"),
            f"http://127.0.0.1:{server.server_port}",
        ),
    )
    process.start()
    try:
        assert accepted.wait(10), "synthetic model never received the request"
        process.kill()
        process.join(5)
        assert process.exitcode == -9
        release.set()
        time.sleep(0.8)

        async def reopen():
            state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)

            async def authorize(item):
                return True

            async def recover(item):
                return None

            async def forbidden(*args, **kwargs):
                calls.append(1)
                raise AssertionError("uncertain generation was repeated")

            flow = HttpWorkflow(
                state,
                authorize=authorize,
                recover=recover,
                answer=forbidden,
                lease_seconds=0.6,
                poll_interval=0.05,
            )
            try:
                await flow.admit(item)
                with pytest.raises(HttpWorkRejected) as error:
                    await flow.wait(item, timeout=5)
                assert error.value.code == "answer_outcome_unknown"
            finally:
                await flow.aclose()
                state.close()

        asyncio.run(reopen())
        assert calls == [1]
    finally:
        release.set()
        if process.is_alive():
            process.kill()
        process.join(5)
        server.shutdown()
        server.server_close()
        thread.join()
