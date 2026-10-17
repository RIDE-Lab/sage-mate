from pathlib import Path
import subprocess

import pytest

from sage_faculty_twin.code_workbench import CODE_WORKBENCH_PROFILES, CodeWorkbench
from sage_faculty_twin.code_session_store import CodeSessionStore
from sage_faculty_twin.config import AppSettings, settings
from sage_faculty_twin.models import (
    ChatRequest,
    CodeAssistRequest,
    CodeCommandRequest,
    CodeContextRequest,
    CodeDirectoryListRequest,
    CodeFileReadRequest,
    CodeGitDiffRequest,
    CodeGitStatusRequest,
    CodeProposeRequest,
    CodeSessionAppendMessageRequest,
    CodeSessionCreateRequest,
    CodeSessionMessage,
    CodeSearchRequest,
    KnowledgeSearchHit,
)


def _workbench(tmp_path: Path) -> CodeWorkbench:
    workspace = tmp_path / "project-a"
    workspace.mkdir(parents=True)
    return CodeWorkbench(
        settings.model_copy(
            update={
                "deployment_mode": "local_code",
                "app_profile": "code_assistant",
                "code_workbench_enabled": True,
                "code_workspace_roots": str(workspace),
                "code_command_timeout_seconds": 5,
            }
        )
    )


def test_code_workbench_lists_only_local_code_explicit_workspaces(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)

    response = workbench.list_workspaces()
    workspace_ids = {workspace.workspace_id for workspace in response.workspaces}

    assert workspace_ids == {"project-a"}


def test_code_workbench_hosted_mode_exposes_no_workspaces(tmp_path: Path) -> None:
    workspace = tmp_path / "project-a"
    workspace.mkdir()
    workbench = CodeWorkbench(
        settings.model_copy(
            update={
                "deployment_mode": "hosted",
                "app_profile": "code_assistant",
                "code_workspace_roots": str(workspace),
                "code_workbench_enabled": True,
            }
        )
    )

    response = workbench.list_workspaces()

    assert response.workspaces == []


def test_code_workbench_legacy_managed_root_ignores_symlink_escapes(tmp_path: Path) -> None:
    managed_root = tmp_path / "code-workspaces"
    managed_root.mkdir()
    (managed_root / "project-a").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (managed_root / "outside-link").symlink_to(outside, target_is_directory=True)

    workbench = CodeWorkbench(
        settings.model_copy(
            update={
                "deployment_mode": "local_code",
                "app_profile": "code_assistant",
                "code_workspace_roots": "",
                "code_workspace_root": managed_root,
                "code_workbench_enabled": True,
            }
        )
    )

    response = workbench.list_workspaces()
    workspace_ids = {workspace.workspace_id for workspace in response.workspaces}

    assert workspace_ids == {"project-a"}


def test_code_workbench_faculty_twin_profile_exposes_no_workspaces(tmp_path: Path) -> None:
    workspace = tmp_path / "project-a"
    workspace.mkdir()
    workbench = CodeWorkbench(
        settings.model_copy(
            update={
                "deployment_mode": "local_code",
                "app_profile": "faculty_twin",
                "code_workspace_roots": str(workspace),
                "code_workbench_enabled": True,
            }
        )
    )

    response = workbench.list_workspaces()

    assert response.workspaces == []


def test_code_workbench_auto_scientist_profile_exposes_workspaces(tmp_path: Path) -> None:
    workspace = tmp_path / "project-a"
    workspace.mkdir()
    workbench = CodeWorkbench(
        settings.model_copy(
            update={
                "deployment_mode": "local_code",
                "app_profile": "auto_scientist",
                "code_workspace_roots": str(workspace),
                "code_workbench_enabled": True,
            }
        )
    )

    response = workbench.list_workspaces()

    assert [workspace.workspace_id for workspace in response.workspaces] == ["project-a"]


def test_code_workbench_formats_empty_workspace_state(tmp_path: Path) -> None:
    workbench = CodeWorkbench(
        settings.model_copy(
            update={
                "deployment_mode": "local_code",
                "app_profile": "code_assistant",
                "code_workspace_roots": "",
                "code_workspace_root": tmp_path / "empty-managed-root",
                "code_workbench_enabled": True,
            }
        )
    )

    message = workbench.format_workspaces_for_chat()

    assert "还没有配置可用代码 workspace" in message
    assert "Workspace Folders" in message
    assert "~/tmp/faculty-twin-demo-project" in message


def test_code_session_store_persists_runtime_private_records(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime-private"
    store = CodeSessionStore(
        settings.model_copy(
            update={
                "runtime_dir": runtime_dir,
                "code_session_dir": runtime_dir / "data" / "code_sessions",
            }
        )
    )

    session = store.create_session(
        CodeSessionCreateRequest(
            workspace_id="project-a",
            title="Fix cart",
            initial_message=CodeSessionMessage(role="user", content="Please inspect cart.py"),
        ),
        default_backend="internal",
    )
    updated = store.append_message(
        session.session_id,
        CodeSessionAppendMessageRequest(
            role="assistant",
            content="Found a likely rounding issue.",
            last_proposal_summary="Use Decimal for currency math.",
        ),
    )

    assert updated is not None
    assert updated.workspace_id == "project-a"
    assert updated.backend == "internal"
    assert updated.last_proposal_summary == "Use Decimal for currency math."
    assert len(updated.messages) == 2
    assert (runtime_dir / "data" / "code_sessions" / f"{session.session_id}.json").is_file()

    reloaded = CodeSessionStore(
        settings.model_copy(
            update={
                "runtime_dir": runtime_dir,
                "code_session_dir": runtime_dir / "data" / "code_sessions",
            }
        )
    )
    assert reloaded.get_session(session.session_id) == updated
    assert [item.session_id for item in reloaded.list_sessions()] == [session.session_id]


def test_code_session_settings_default_to_runtime_private_dir(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime-private"
    local_settings = AppSettings(runtime_dir=runtime_dir)

    assert local_settings.code_session_dir == runtime_dir / "data" / "code_sessions"
    assert not str(local_settings.code_session_dir).startswith(str(Path.cwd() / "data"))


def test_code_workbench_searches_and_reads_files(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)
    workspace_root = tmp_path / "project-a"
    source = workspace_root / "src" / "demo.py"
    source.parent.mkdir()
    source.write_text("def hello():\n    return 'faculty twin'\n", encoding="utf-8")

    search_response = workbench.search(
        CodeSearchRequest(workspace_id="project-a", query="faculty twin")
    )
    read_response = workbench.read_file(
        CodeFileReadRequest(workspace_id="project-a", path="src/demo.py", max_lines=1)
    )

    assert search_response.hits[0].path == "src/demo.py"
    assert search_response.hits[0].line_number == 2
    assert read_response.content == "def hello():"
    assert read_response.truncated is True


def test_code_workbench_lists_directory_entries(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)
    workspace_root = tmp_path / "project-a"
    (workspace_root / "src").mkdir()
    (workspace_root / "README.md").write_text("# Demo\n", encoding="utf-8")

    response = workbench.list_directory(
        CodeDirectoryListRequest(workspace_id="project-a", path=".")
    )

    entries = {entry.path: entry for entry in response.entries}
    assert entries["src"].kind == "directory"
    assert entries["README.md"].kind == "file"
    assert entries["README.md"].size_bytes == 7


def test_code_workbench_blocks_path_escape_and_dangerous_commands(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)

    try:
        workbench.read_file(CodeFileReadRequest(workspace_id="project-a", path="../secret.txt"))
    except ValueError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("path escape should fail")

    try:
        workbench.list_directory(
            CodeDirectoryListRequest(workspace_id="project-a", path="../")
        )
    except ValueError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("directory path escape should fail")

    response = workbench.run_command(
        CodeCommandRequest(workspace_id="project-a", command="rm -rf .")
    )

    assert response.blocked is True
    assert response.exit_code == 126


def test_code_workbench_runs_read_only_allowlisted_command(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)
    (tmp_path / "project-a" / "README.md").write_text(
        "# Demo\n",
        encoding="utf-8",
    )

    response = workbench.run_command(
        CodeCommandRequest(workspace_id="project-a", command="rg Demo README.md")
    )

    assert response.blocked is False
    assert response.exit_code == 0
    assert "# Demo" in response.stdout


def test_code_run_full_approval_mode_allows_write_intent(tmp_path: Path) -> None:
    from sage_faculty_twin.service import DigitalTwinService

    workbench = _workbench(tmp_path)
    workspace_root = tmp_path / "project-a"
    (workspace_root / "demo.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=workspace_root, check=True, capture_output=True)

    service = object.__new__(DigitalTwinService)
    service._settings = workbench._settings
    service._code_workbench = workbench
    service._llm_client = type("StubLlm", (), {"model_name": "stub-model"})()

    blocked = service.run_code_command(
        CodeCommandRequest(
            workspace_id="project-a",
            command="git add demo.py",
            code_approval_mode="ask",
        )
    )
    allowed = service.run_code_command(
        CodeCommandRequest(
            workspace_id="project-a",
            command="git add demo.py",
            code_approval_mode="full",
        )
    )

    assert blocked.blocked is True
    assert "allow_write" in blocked.message
    assert allowed.blocked is False
    assert allowed.exit_code == 0


def test_code_workbench_reports_git_status_and_diff(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)
    workspace_root = tmp_path / "project-a"
    subprocess.run(["git", "init"], cwd=workspace_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=workspace_root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=workspace_root,
        check=True,
    )
    readme = workspace_root / "README.md"
    readme.write_text("# Demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=workspace_root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=workspace_root, check=True)
    readme.write_text("# Demo\nchanged\n", encoding="utf-8")

    status = workbench.git_status(CodeGitStatusRequest(workspace_id="project-a"))
    diff = workbench.git_diff(CodeGitDiffRequest(workspace_id="project-a", path="README.md"))

    assert status.is_git_repo is True
    assert status.clean is False
    assert "README.md" in status.porcelain
    assert "+changed" in diff.diff


def test_code_workbench_builds_context_pack(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)
    (tmp_path / "project-a" / "README.md").write_text(
        "# Demo\nContext pack\n",
        encoding="utf-8",
    )

    response = workbench.build_context(
        CodeContextRequest(workspace_id="project-a", paths=["README.md"])
    )

    assert "Workspace" in response.context
    assert "Git Status" in response.context
    assert "Context pack" in response.context
    assert response.context_paths == ["README.md"]


def test_code_workbench_builds_default_context_when_paths_are_empty(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)
    workspace_root = tmp_path / "project-a"
    (workspace_root / "README.md").write_text("# Demo\nProject overview\n", encoding="utf-8")
    (workspace_root / "src").mkdir()
    (workspace_root / "src" / "demo.py").write_text(
        "def hello():\n    return 'faculty twin'\n",
        encoding="utf-8",
    )

    response = workbench.build_context(CodeContextRequest(workspace_id="project-a", paths=[]))

    assert "Workspace Overview" in response.context
    assert "src/" in response.context
    assert "Project overview" in response.context
    assert "return 'faculty twin'" in response.context
    assert "README.md" in response.context_paths
    assert "src/demo.py" in response.context_paths


def test_code_workbench_builds_assist_prompt_with_file_context(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)
    (tmp_path / "project-a" / "README.md").write_text(
        "# Demo\nCode-agent helper\n",
        encoding="utf-8",
    )

    system_prompt, user_prompt, context_paths = workbench.build_assist_prompt(
        CodeAssistRequest(
            workspace_id="project-a",
            task="How should I extend this repo?",
            paths=["README.md"],
        )
    )

    assert "code-agent style" in system_prompt
    assert "README.md" in context_paths
    assert "Git Status" in user_prompt
    assert "Code-agent helper" in user_prompt


def test_code_workbench_builds_propose_prompt_with_status_diff_and_files(
    tmp_path: Path,
) -> None:
    workbench = _workbench(tmp_path)
    workspace_root = tmp_path / "project-a"
    subprocess.run(["git", "init"], cwd=workspace_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=workspace_root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=workspace_root,
        check=True,
    )
    source = workspace_root / "demo.py"
    source.write_text("def hello():\n    return 'old'\n", encoding="utf-8")
    subprocess.run(["git", "add", "demo.py"], cwd=workspace_root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=workspace_root, check=True)
    source.write_text("def hello():\n    return 'new'\n", encoding="utf-8")

    system_prompt, user_prompt, context_paths = workbench.build_propose_prompt(
        CodeProposeRequest(
            workspace_id="project-a",
            task="Change hello return value safely",
            paths=["demo.py"],
        )
    )

    assert "propose-only" in system_prompt
    assert "Workspace: project-a" in user_prompt
    assert "# Git Status" in user_prompt
    assert "# Git Diff" in user_prompt
    assert "+    return 'new'" in user_prompt
    assert "# File Context" in user_prompt
    assert "return 'new'" in user_prompt
    assert context_paths == ["demo.py"]


def test_code_workbench_propose_does_not_modify_files(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)
    source = tmp_path / "project-a" / "demo.py"
    source.write_text("print('unchanged')\n", encoding="utf-8")
    before = source.read_text(encoding="utf-8")

    workbench.build_propose_prompt(
        CodeProposeRequest(
            workspace_id="project-a",
            task="Propose a logging change",
            paths=["demo.py"],
        )
    )

    assert source.read_text(encoding="utf-8") == before


def test_code_workbench_propose_blocks_path_escape(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)

    try:
        workbench.build_propose_prompt(
            CodeProposeRequest(
                workspace_id="project-a",
                task="Read outside file",
                paths=["../secret.txt"],
            )
        )
    except ValueError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("propose path escape should fail")


@pytest.mark.asyncio
async def test_code_service_propose_calls_llm_without_modifying_files(tmp_path: Path) -> None:
    from sage_faculty_twin.service import DigitalTwinService

    class StubLlmClient:
        model_name = "stub-model"

        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def answer_question_sync(self, system_prompt: str, user_prompt: str, **_: object) -> str:
            self.calls.append((system_prompt, user_prompt))
            return (
                '{"summary":"Add greeting","affected_files":["demo.py"],'
                '"unified_diff":"--- a/demo.py\\n+++ b/demo.py\\n@@ -1 +1 @@\\n-print(1)\\n+print(2)\\n",'
                '"risks":"Low risk.","suggested_tests":["pytest tests/test_demo.py -q"]}'
            )

    service = object.__new__(DigitalTwinService)
    service._settings = settings.model_copy(
        update={
            "deployment_mode": "local_code",
            "app_profile": "code_assistant",
            "code_workbench_enabled": True,
            "code_workspace_roots": str(tmp_path / "project-a"),
            "code_agent_backend": "internal",
        }
    )
    (tmp_path / "project-a").mkdir()
    source = tmp_path / "project-a" / "demo.py"
    source.write_text("print(1)\n", encoding="utf-8")
    service._code_workbench = CodeWorkbench(service._settings)
    service._llm_client = StubLlmClient()

    response = await service.propose_code_change(
        CodeProposeRequest(
            workspace_id="project-a",
            task="Change the printed value",
            paths=["demo.py"],
        )
    )

    assert response.summary == "Add greeting"
    assert response.affected_files == ["demo.py"]
    assert "+print(2)" in response.unified_diff
    assert response.suggested_tests == ["pytest tests/test_demo.py -q"]
    assert source.read_text(encoding="utf-8") == "print(1)\n"
    assert service._llm_client.calls
    assert "Git Status" in service._llm_client.calls[0][1]
    assert [step.key for step in response.workflow_trace] == [
        "code_workspace",
        "code_context",
        "code_agent_backend",
        "code_result",
    ]
    assert response.workflow_trace[2].summary == (
        "已通过 Sage Mate 内置 harness 执行修改建议流程。"
    )


@pytest.mark.asyncio
async def test_auto_scientist_profile_bootstraps_research_with_code_node(
    tmp_path: Path,
) -> None:
    from sage_faculty_twin.service import DigitalTwinService

    class StubLlmClient:
        model_name = "stub-research-model"
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        def answer_question_sync(self, *_args: object, **_kwargs: object) -> str:
            self.calls.append(str(_args[1] if len(_args) > 1 else ""))
            return "代码节点建议：先阅读 README.md，设计 baseline、sanity check 和 ablation。"

    class StubKnowledgeStore:
        def search(self, *_args: object, **_kwargs: object) -> list[KnowledgeSearchHit]:
            return [
                KnowledgeSearchHit(
                    document_id="paper-1",
                    title="Prefix cache 研究背景",
                    excerpt=(
                        "组内已有记录建议优先比较 TTFT、吞吐和缓存命中率。"
                        * 80
                    ),
                    score=42.0,
                    tags=["publications", "profile"],
                )
            ]

    workspace = tmp_path / "project-a"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo research repo\n", encoding="utf-8")

    service = object.__new__(DigitalTwinService)
    service._settings = settings.model_copy(
        update={
            "deployment_mode": "local_code",
            "app_profile": "auto_scientist",
            "code_workbench_enabled": True,
            "code_workspace_roots": str(workspace),
            "code_agent_backend": "internal",
            "model_name": "stub-research-model",
        }
    )
    service._code_workbench = CodeWorkbench(service._settings)
    service._llm_client = StubLlmClient()
    service._knowledge_store = StubKnowledgeStore()
    service._knowledge_retriever = service._knowledge_store

    response = await service._answer_auto_scientist(
        ChatRequest(
            student_name="guest",
            question="我想研究 prefix cache 对 RAG 推理吞吐的影响",
            visitor_profile="lab_member",
            deep_thinking=False,
        )
    )

    assert response.workflow_action == "auto_scientist"
    assert "自动科学家启动包" in response.answer
    assert "prefix cache" in response.answer
    assert "代码节点建议" in response.answer
    assert "Prefix cache 研究背景" in response.answer
    assert "TTFT" in response.answer
    assert "确定性代码态势" in response.answer
    assert "README.md" in response.answer
    assert "`project-a`" in response.answer
    assert any(step.key == "code_agent_backend" for step in response.workflow_trace)
    assert any(step.key == "auto_scientist_twin_context" for step in response.workflow_trace)
    assert service._llm_client.calls
    assert "分身上下文" in service._llm_client.calls[0]


@pytest.mark.asyncio
async def test_auto_scientist_falls_back_when_code_node_degenerates(
    tmp_path: Path,
) -> None:
    from sage_faculty_twin.service import DigitalTwinService

    class DegenerateLlmClient:
        model_name = "stub-research-model"

        def answer_question_sync(self, *_args: object, **_kwargs: object) -> str:
            return " ".join(["animations"] * 300)

    class EmptyKnowledgeStore:
        def search(self, *_args: object, **_kwargs: object) -> list[KnowledgeSearchHit]:
            return []

    workspace = tmp_path / "vllm-hust"
    workspace.mkdir()
    (workspace / "tests" / "v1" / "core").mkdir(parents=True)
    (workspace / "benchmarks").mkdir()
    (workspace / "tests" / "v1" / "core" / "test_prefix_caching.py").write_text(
        "# prefix cache test\n",
        encoding="utf-8",
    )
    (workspace / "benchmarks" / "benchmark_long_document_qa_throughput.py").write_text(
        "# benchmark long document QA throughput\n"
        "ENABLE_PREFIX_CACHING = True\n"
        "METRICS = ['TTFT', 'throughput']\n",
        encoding="utf-8",
    )
    (workspace / "config.py").write_text(
        "max_num_seqs = 64\n",
        encoding="utf-8",
    )

    service = object.__new__(DigitalTwinService)
    service._settings = settings.model_copy(
        update={
            "deployment_mode": "local_code",
            "app_profile": "auto_scientist",
            "code_workbench_enabled": True,
            "code_workspace_roots": str(workspace),
            "code_agent_backend": "internal",
            "model_name": "stub-research-model",
        }
    )
    service._code_workbench = CodeWorkbench(service._settings)
    service._llm_client = DegenerateLlmClient()
    service._knowledge_store = EmptyKnowledgeStore()

    response = await service._answer_auto_scientist(
        ChatRequest(
            student_name="guest",
            question="优化 vllm-hust long document QA throughput",
            visitor_profile="lab_member",
            deep_thinking=False,
        )
    )

    assert "代码研究节点输出质量不足" in response.answer
    assert "确定性代码态势" in response.answer
    assert "benchmarks/benchmark_long_document_qa_throughput.py" in response.answer
    assert (
        response.answer.index("benchmarks/benchmark_long_document_qa_throughput.py")
        < response.answer.index("tests/v1/core/test_prefix_caching.py")
    )
    assert "TTFT" in response.answer
    assert any(
        step.key == "auto_scientist_code_quality_gate"
        for step in response.workflow_trace
    )


@pytest.mark.asyncio
async def test_code_service_can_delegate_ask_to_claude_hust_backend(tmp_path: Path) -> None:
    from sage_faculty_twin.code_agent_backends import CodeAgentBackendResult
    from sage_faculty_twin.service import DigitalTwinService

    class StubClaudeBackend:
        def assist(self, request: CodeAssistRequest) -> CodeAgentBackendResult:
            assert request.workspace_id == "project-a"
            return CodeAgentBackendResult(
                answer="delegated answer",
                context_paths=request.paths,
                used_model="stub-code-model",
                backend="claude_hust",
            )

    service = object.__new__(DigitalTwinService)
    service._settings = settings.model_copy(
        update={
            "deployment_mode": "local_code",
            "app_profile": "code_assistant",
            "code_workbench_enabled": True,
            "code_workspace_roots": str(tmp_path / "project-a"),
            "code_agent_backend": "claude_hust",
            "model_name": "stub-code-model",
        }
    )
    (tmp_path / "project-a").mkdir()
    source = tmp_path / "project-a" / "demo.py"
    source.write_text("print(1)\n", encoding="utf-8")
    service._code_workbench = CodeWorkbench(service._settings)
    service._llm_client = type("StubLlm", (), {"model_name": "internal-model"})()
    service._code_agent_backend = lambda: StubClaudeBackend()

    response = await service.assist_with_code(
        CodeAssistRequest(workspace_id="project-a", task="Explain this repo")
    )

    assert response.answer == "delegated answer"
    assert response.used_model == "stub-code-model"
    assert source.read_text(encoding="utf-8") == "print(1)\n"


def test_claude_hust_backend_propose_uses_temp_copy(tmp_path: Path) -> None:
    from sage_faculty_twin.code_agent_backends import ClaudeHustCodeAgentBackend

    workspace = tmp_path / "project-a"
    workspace.mkdir()
    source = workspace / "demo.py"
    source.write_text("print(1)\n", encoding="utf-8")

    backend_settings = settings.model_copy(
        update={
            "deployment_mode": "local_code",
            "app_profile": "code_assistant",
            "code_workbench_enabled": True,
            "code_workspace_roots": str(workspace),
            "code_agent_backend": "claude_hust",
            "model_name": "stub-code-model",
        }
    )
    touched_paths: list[Path] = []

    def fake_print(_prompt: str, cwd: Path, *, output_format: str = "text") -> str:
        assert output_format == "json"
        touched_paths.append(cwd)
        (cwd / "demo.py").write_text("print(2)\n", encoding="utf-8")
        return (
            '{"content":[{"type":"text","text":"{\\"summary\\":\\"Change demo\\",'
            '\\"affected_files\\":[\\"demo.py\\"],'
            '\\"unified_diff\\":\\"--- a/demo.py\\\\n+++ b/demo.py\\\\n@@ -1 +1 @@\\\\n-print(1)\\\\n+print(2)\\\\n\\",'
            '\\"risks\\":\\"Low\\",\\"suggested_tests\\":[\\"python demo.py\\"]}"}]}'
        )

    backend = ClaudeHustCodeAgentBackend(
        settings=backend_settings,
        workbench=CodeWorkbench(backend_settings),
        llm_client=type("StubLlm", (), {"model_name": "internal-model"})(),
        print_runner=fake_print,
    )

    result = backend.propose(
        CodeProposeRequest(workspace_id="project-a", task="Change demo")
    )

    assert touched_paths
    assert touched_paths[0] != workspace
    assert source.read_text(encoding="utf-8") == "print(1)\n"
    assert '"summary":"Change demo"' in result.answer
    assert "+print(2)" in result.answer
    assert result.context_paths == ["demo.py"]
    assert result.backend == "claude_hust"


def test_claude_hust_openai_proxy_sets_local_api_key(tmp_path: Path) -> None:
    from sage_faculty_twin.code_agent_backends import ClaudeHustCodeAgentBackend

    workspace = tmp_path / "project-a"
    workspace.mkdir()
    backend_settings = settings.model_copy(
        update={
            "deployment_mode": "local_code",
            "app_profile": "code_assistant",
            "code_workbench_enabled": True,
            "code_workspace_roots": str(workspace),
            "code_agent_backend": "claude_hust",
            "llm_base_url": "http://127.0.0.1:8000/v1",
            "api_key": "",
            "model_name": "mlx-community/Qwen2.5-7B-Instruct-4bit",
        }
    )
    backend = ClaudeHustCodeAgentBackend(
        settings=backend_settings,
        workbench=CodeWorkbench(backend_settings),
        llm_client=type("StubLlm", (), {"model_name": "internal-model"})(),
        print_runner=lambda *_args, **_kwargs: "",
    )

    env = backend._env()

    assert env["CLAUDE_CODE_FORCE_RECOVERY_CLI"] == "1"
    assert env["ANTHROPIC_API_KEY"] == "sage-mate-local"


def test_claude_hust_proxy_budgets_output_for_4k_local_model(tmp_path: Path) -> None:
    from sage_faculty_twin.code_agent_backends import ClaudeHustCodeAgentBackend

    workspace = tmp_path / "project-a"
    workspace.mkdir()
    backend_settings = settings.model_copy(
        update={
            "deployment_mode": "local_code",
            "app_profile": "code_assistant",
            "code_workbench_enabled": True,
            "code_workspace_roots": str(workspace),
            "code_agent_backend": "claude_hust",
            "llm_base_url": "http://127.0.0.1:8000/v1",
            "model_name": "mlx-community/Qwen2.5-7B-Instruct-4bit",
        }
    )
    backend = ClaudeHustCodeAgentBackend(
        settings=backend_settings,
        workbench=CodeWorkbench(backend_settings),
        llm_client=type("StubLlm", (), {"model_name": "internal-model"})(),
        print_runner=lambda *_args, **_kwargs: "",
    )

    messages = [{"role": "user", "content": "x" * 9219}]

    assert backend._bounded_openai_max_tokens(1024, messages) == 768
    assert backend._bounded_openai_max_tokens(
        1024,
        [{"role": "user", "content": "x" * 12000}],
    ) == 64


def test_claude_hust_assist_bounds_context_for_local_openai_proxy(tmp_path: Path) -> None:
    from sage_faculty_twin.code_agent_backends import ClaudeHustCodeAgentBackend

    workspace = tmp_path / "project-a"
    workspace.mkdir()
    (workspace / "README.md").write_text("x" * 20000, encoding="utf-8")
    backend_settings = settings.model_copy(
        update={
            "deployment_mode": "local_code",
            "app_profile": "code_assistant",
            "code_workbench_enabled": True,
            "code_workspace_roots": str(workspace),
            "code_agent_backend": "claude_hust",
            "llm_base_url": "http://127.0.0.1:8000/v1",
            "model_name": "mlx-community/Qwen2.5-7B-Instruct-4bit",
        }
    )
    seen_prompts: list[str] = []

    def fake_print(prompt: str, _cwd: Path, *, output_format: str = "text") -> str:
        seen_prompts.append(prompt)
        return '{"content":[{"type":"text","text":"ok"}]}'

    backend = ClaudeHustCodeAgentBackend(
        settings=backend_settings,
        workbench=CodeWorkbench(backend_settings),
        llm_client=type("StubLlm", (), {"model_name": "internal-model"})(),
        print_runner=fake_print,
    )

    backend.assist(
        CodeAssistRequest(
            workspace_id="project-a",
            task="Analyze",
            paths=["README.md"],
            max_context_chars=30000,
        )
    )

    assert seen_prompts
    assert len(seen_prompts[0]) < 9000


def test_claude_hust_fails_fast_when_local_model_endpoint_is_not_ready(
    tmp_path: Path,
) -> None:
    from sage_faculty_twin.code_agent_backends import ClaudeHustCodeAgentBackend

    workspace = tmp_path / "project-a"
    workspace.mkdir()
    backend_settings = settings.model_copy(
        update={
            "deployment_mode": "local_code",
            "app_profile": "code_assistant",
            "code_workbench_enabled": True,
            "code_workspace_roots": str(workspace),
            "code_agent_backend": "claude_hust",
            "llm_base_url": "http://127.0.0.1:9/v1",
            "model_name": "mlx-community/Qwen2.5-7B-Instruct-4bit",
        }
    )
    backend = ClaudeHustCodeAgentBackend(
        settings=backend_settings,
        workbench=CodeWorkbench(backend_settings),
        llm_client=type("StubLlm", (), {"model_name": "internal-model"})(),
    )

    with pytest.raises(RuntimeError, match="本地代码模型还没有就绪"):
        backend._ensure_local_openai_endpoint_ready()


def test_claude_hust_json_output_extracts_text_tools_and_diff(tmp_path: Path) -> None:
    from sage_faculty_twin.code_agent_backends import ClaudeHustCodeAgentBackend

    calls: list[str] = []

    def fake_print(_prompt: str, _cwd: Path, *, output_format: str = "text") -> str:
        calls.append(output_format)
        return (
            '{"content":[{"type":"text","text":"Done"}],'
            '"events":[{"type":"tool_use","name":"Read","input":{"file_path":"demo.py"}}],'
            '"unified_diff":"--- a/demo.py\\n+++ b/demo.py\\n@@ -1 +1 @@\\n-print(1)\\n+print(2)\\n"}'
        )

    backend = ClaudeHustCodeAgentBackend(
        settings=settings,
        workbench=_workbench(tmp_path),
        llm_client=type("StubLlm", (), {"model_name": "internal-model"})(),
        print_runner=fake_print,
    )

    result = backend._run_structured_or_text("task", tmp_path)

    assert calls == ["json"]
    assert "Done" in result
    assert "+print(2)" in result
    assert "Inspected files: demo.py" in result
    assert "Tool calls: Read" in result


def test_claude_hust_text_fallback_when_json_output_unavailable(tmp_path: Path) -> None:
    from sage_faculty_twin.code_agent_backends import ClaudeHustCodeAgentBackend

    calls: list[str] = []

    def fake_print(_prompt: str, _cwd: Path, *, output_format: str = "text") -> str:
        calls.append(output_format)
        if output_format == "json":
            raise RuntimeError("Error: invalid --output-format json")
        return "plain fallback"

    backend = ClaudeHustCodeAgentBackend(
        settings=settings,
        workbench=_workbench(tmp_path),
        llm_client=type("StubLlm", (), {"model_name": "internal-model"})(),
        print_runner=fake_print,
    )

    result = backend._run_structured_or_text("task", tmp_path)

    assert result == "plain fallback"
    assert calls == ["json", "text"]


@pytest.mark.asyncio
async def test_code_chat_propose_command_exposes_workflow_trace(tmp_path: Path) -> None:
    from sage_faculty_twin.service import DigitalTwinService

    class StubLlmClient:
        model_name = "stub-model"

        def answer_question_sync(self, *_args: object, **_kwargs: object) -> str:
            return (
                '{"summary":"Patch demo","affected_files":["demo.py"],'
                '"unified_diff":"--- a/demo.py\\n+++ b/demo.py\\n@@ -1 +1 @@\\n-print(1)\\n+print(2)\\n",'
                '"risks":"Low","suggested_tests":["pytest -q"]}'
            )

    workspace = tmp_path / "project-a"
    workspace.mkdir()
    (workspace / "demo.py").write_text("print(1)\n", encoding="utf-8")

    service = object.__new__(DigitalTwinService)
    service._settings = settings.model_copy(
        update={
            "deployment_mode": "local_code",
            "app_profile": "code_assistant",
            "code_workbench_enabled": True,
            "code_workspace_roots": str(workspace),
            "code_agent_backend": "internal",
        }
    )
    service._code_workbench = CodeWorkbench(service._settings)
    service._llm_client = StubLlmClient()

    response = await service._answer_code_workbench_command(
        ChatRequest(
            student_name="guest",
            question="/code propose project-a change print -- demo.py",
        )
    )

    assert "Patch demo" in response.answer
    assert response.workflow_action == "code_workbench"
    assert [step.key for step in response.workflow_trace] == [
        "code_workspace",
        "code_context",
        "code_agent_backend",
        "code_result",
    ]


def test_code_workbench_parses_chat_commands(tmp_path: Path) -> None:
    workbench = _workbench(tmp_path)

    search = workbench.parse_chat_command("/code search twin CodeWorkbench --glob '*.py'")
    listing = workbench.parse_chat_command("/code ls twin src")
    read = workbench.parse_chat_command("/code read twin src/app.py 10 30")
    status = workbench.parse_chat_command("/code status twin")
    diff = workbench.parse_chat_command("/code diff twin src/app.py --staged")
    context = workbench.parse_chat_command("/code context twin README.md src/app.py")
    run = workbench.parse_chat_command("/code run twin rg CodeWorkbench src")
    ask = workbench.parse_chat_command("/code ask twin explain this -- src/a.py src/b.py")
    propose = workbench.parse_chat_command(
        "/code propose twin fix parser -- src/a.py src/b.py"
    )
    chinese = workbench.parse_chat_command("代码 搜 twin 工作台")
    doctor = workbench.parse_chat_command("/code doctor")
    chinese_doctor = workbench.parse_chat_command("代码 诊断")

    assert workbench.is_chat_command("/code workspaces") is True
    assert search.action == "search"
    assert search.query == "CodeWorkbench"
    assert search.glob == "*.py"
    assert listing.action == "list"
    assert listing.path == "src"
    assert read.start_line == 10
    assert read.max_lines == 30
    assert status.action == "status"
    assert diff.action == "diff"
    assert diff.path == "src/app.py"
    assert diff.staged is True
    assert context.action == "context"
    assert context.paths == ["README.md", "src/app.py"]
    assert run.command == "rg CodeWorkbench src"
    assert ask.action == "ask"
    assert ask.task == "explain this"
    assert ask.paths == ["src/a.py", "src/b.py"]
    assert propose.action == "propose"
    assert propose.task == "fix parser"
    assert propose.paths == ["src/a.py", "src/b.py"]
    assert chinese.action == "search"
    assert chinese.query == "工作台"
    assert doctor.action == "doctor"
    assert chinese_doctor.action == "doctor"


def test_code_workbench_doctor_reports_ok_warn_and_error(tmp_path: Path) -> None:
    workspace = tmp_path / "project-a"
    workspace.mkdir()
    cli = tmp_path / "claude-hust"
    cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cli.chmod(0o755)

    healthy = CodeWorkbench(
        settings.model_copy(
            update={
                "deployment_mode": "local_code",
                "app_profile": "code_assistant",
                "code_workbench_enabled": True,
                "code_workspace_roots": str(workspace),
                "code_agent_backend": "claude_hust",
                "claude_hust_cli_path": str(cli),
                "llm_base_url": "http://127.0.0.1:8000/v1",
                "model_name": "qwen-code",
            }
        )
    )

    healthy_report = healthy.format_doctor_for_chat()

    assert "代码工作台诊断报告" in healthy_report
    assert "总体状态：OK" in healthy_report
    assert "[OK] DIGITAL_TWIN_DEPLOYMENT_MODE：local_code" in healthy_report
    assert "[OK] workspace allowlist：1 个可用 workspace" in healthy_report
    assert "[OK] DIGITAL_TWIN_CLAUDE_HUST_CLI_PATH" in healthy_report

    missing_llm = CodeWorkbench(
        settings.model_copy(
            update={
                "deployment_mode": "local_code",
                "app_profile": "code_assistant",
                "code_workbench_enabled": True,
                "code_workspace_roots": str(workspace),
                "code_agent_backend": "internal",
                "claude_hust_cli_path": "",
                "llm_base_url": "",
                "model_name": "",
            }
        )
    )

    missing_report = missing_llm.format_doctor_for_chat()

    assert "总体状态：ERROR" in missing_report
    assert "[WARN] DIGITAL_TWIN_CLAUDE_HUST_CLI_PATH" in missing_report
    assert "[ERROR] DIGITAL_TWIN_LLM_BASE_URL：未配置。" in missing_report
    assert "[ERROR] DIGITAL_TWIN_MODEL_NAME：未配置。" in missing_report


@pytest.mark.asyncio
async def test_code_chat_doctor_runs_diagnostics(tmp_path: Path) -> None:
    from sage_faculty_twin.service import DigitalTwinService

    workbench = _workbench(tmp_path)
    service = object.__new__(DigitalTwinService)
    service._settings = workbench._settings
    service._code_workbench = workbench
    service._llm_client = type("StubLlm", (), {"model_name": "stub-model"})()

    response = await service._answer_code_workbench_command(
        ChatRequest(student_name="tester", question="/code doctor")
    )

    assert "代码工作台诊断报告" in response.answer
    assert "代码工作台可用命令" not in response.answer


def test_code_doctor_not_available_in_hosted_web_mode(tmp_path: Path) -> None:
    workspace = tmp_path / "project-a"
    workspace.mkdir()
    hosted_settings = settings.model_copy(
        update={
            "deployment_mode": "hosted",
            "app_profile": "code_assistant",
            "code_workbench_enabled": True,
            "code_workspace_roots": str(workspace),
            "model_name": "stub-code-model",
        }
    )

    workbench = CodeWorkbench(hosted_settings)

    exposed_in_chat = (
        hosted_settings.deployment_mode == "local_code"
        and hosted_settings.code_workbench_enabled
        and hosted_settings.app_profile in CODE_WORKBENCH_PROFILES
    )

    assert exposed_in_chat is False
    assert workbench.list_workspaces().workspaces == []


def test_code_api_rejects_hosted_mode_before_admin_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from sage_faculty_twin import api

    monkeypatch.setattr(api.settings, "deployment_mode", "hosted")
    monkeypatch.setattr(api.settings, "app_profile", "code_assistant")
    monkeypatch.setattr(api.settings, "code_workbench_enabled", True)

    with pytest.raises(HTTPException) as exc_info:
        api.require_code_access(None)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 403
