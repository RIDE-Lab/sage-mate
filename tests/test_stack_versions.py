from sage_faculty_twin.service import build_stack_versions_payload


def test_stack_versions_keep_compatibility_source_and_artifact_identity_separate(
    monkeypatch,
) -> None:
    values = {
        "VLLM_ENGINE_COMPATIBILITY_BASE": "vLLM-Ascend 0.23.0",
        "VLLM_ENGINE_CORE_SOURCE_VERSION": "0.24.0.dev1+gcore",
        "VLLM_ENGINE_CORE_COMMIT": "core-commit",
        "VLLM_ENGINE_PLUGIN_SOURCE_VERSION": "0.0.dev1+gplugin",
        "VLLM_ENGINE_PLUGIN_COMMIT": "plugin-commit",
        "VLLM_ENGINE_IMAGE": "example/runtime:derived",
        "VLLM_ENGINE_EXPECTED_IMAGE_ID": "sha256:image-id",
        "VLLM_ENGINE_IMAGE_BUILD_TIME": "2026-09-01T00:00:00Z",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    payload = build_stack_versions_payload()

    assert payload["runtime_compatibility_base"] == "vLLM-Ascend 0.23.0"
    assert payload["runtime_core_source_version"] == "0.24.0.dev1+gcore"
    assert payload["runtime_core_commit"] == "core-commit"
    assert payload["runtime_plugin_source_version"] == "0.0.dev1+gplugin"
    assert payload["runtime_plugin_commit"] == "plugin-commit"
    assert payload["engine_image"] == "example/runtime:derived"
    assert payload["engine_image_id"] == "sha256:image-id"
    assert payload["engine_image_build_time"] == "2026-09-01T00:00:00Z"
