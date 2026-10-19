"""Emit content-free REAL SAGE events using synthetic, side-effect-free ports.

PYTHONPATH=src:tests python tools/export_native_workflow_fixture.py > events.json
This is a visual fixture, never production/model/booking acceptance evidence.
"""

import json

from sage.runtime.execution_observation import ExecutionObserver

from sage_faculty_twin.chat_runtime import ChatRuntime
from test_chat_runtime import TraceContext, ports

events = []
runtime = ChatRuntime()
try:
    with ExecutionObserver(events.append).bind():
        runtime.answer(TraceContext([], booking=False), ports("visual-fixture"), timeout=5)
finally:
    runtime.close()
print(json.dumps([event.to_dict() for event in events]))
