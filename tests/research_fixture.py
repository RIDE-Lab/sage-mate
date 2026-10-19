"""Synthetic schema fixture, never model-quality or research evidence."""
import json


def experiment_response(digest):
    common = {"mechanism": "合成测试机制", "control_value": "关闭", "treatment_value": "开启",
        "shared_conditions": {"model":"合成模型", "hardware":"合成设备", "workload":"相同合成轨迹",
            "software":"合成版本", "precision":"bf16", "warmup_operations":0, "replay_seed":42}, "fault_events": []}
    return json.dumps({"digest": digest, "experiments": [
        {**common, "kind": "invariant", "invariant": "每条记录有唯一所有者"},
        {**common, "kind": "performance", "metric": "吞吐量", "direction": "higher",
            "threshold_gain": 0.2, "confidence": 0.95, "target_power": 0.8,
            "sampling_unit": "paired_replay"}]})
