"""Installed dependencies must supply the native runtime, without source overlays."""

from pathlib import Path
import tomllib


def test_runtime_dependency_is_reproducible_and_not_overridden_by_extras():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]
    runtime = [value for value in project["dependencies"]
               if value.split("@", 1)[0].split("[", 1)[0].strip() == "isage"]
    assert len(runtime) == 1
    assert "[delivery-markdown]" in runtime[0]
    ref = runtime[0].rsplit("@", 1)[1]
    assert len(ref) == 40 and all(char in "0123456789abcdef" for char in ref)
    memory = [value for value in project["dependencies"] if value.startswith("isage-neuromem @ ")]
    assert len(memory) == 1
    memory_ref = memory[0].rsplit("@", 1)[1]
    assert len(memory_ref) == 40 and all(char in "0123456789abcdef" for char in memory_ref)
    for requirements in project["optional-dependencies"].values():
        assert not any(value.startswith(("isage ", "isage[", "isage>")) for value in requirements)
        assert not any(value.startswith("isage-neuromem") for value in requirements)


def test_installed_native_runtime_capabilities():
    from sage.runtime.cancellation import RequestCancellation, bind_request_cancellation
    from sage.runtime.flownet import bootstrap_runtime, build_runtime_client
    from sage.runtime.work_dispatcher import WorkDispatcher
    from sage.runtime.work_journal import WorkJournal
    from sage.runtime.delivery_sequence import DeliveryPlan, DeliverySequence
    from sage.runtime.markdown_delivery import plan_markdown
    from sage.neuromem import MemoryEntry
    from sage.neuromem.memory_collection.durable_neural_memory import DurableNeuralMemory

    assert all(callable(symbol) for symbol in (
        RequestCancellation, bind_request_cancellation, bootstrap_runtime,
        build_runtime_client, WorkDispatcher, WorkJournal, MemoryEntry, DurableNeuralMemory,
        DeliveryPlan, DeliverySequence, plan_markdown,
    ))
    from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
    assert callable(StatefulProcessRuntime.update_many)
    assert callable(WorkJournal.prepare_commit)
    assert callable(DurableNeuralMemory.prepare_batch_once)


def test_quickstart_replaces_same_version_runtime_and_checks_capabilities():
    script = (Path(__file__).parents[1] / "quickstart.sh").read_text()
    assert '--no-deps --force-reinstall "$sage_runtime_requirement"' in script
    assert '--no-deps --force-reinstall "$neuromem_requirement"' in script
    assert '"$repo_root/pyproject.toml"' in script
    assert "from sage.runtime.work_dispatcher import WorkDispatcher" in script
    assert "from sage.runtime.work_journal import WorkJournal" in script
