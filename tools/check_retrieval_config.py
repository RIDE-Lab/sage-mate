"""Fail closed before ingestion when it differs from the running app's index config.

Only field names are logged. Process environment and credentials remain in memory.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

FIELDS = (
    'knowledge_backend', 'knowledge_sagevdb_backend', 'sagevdb_backend',
    'sagevdb_anns_algorithm', 'sagevdb_dimension', 'sagevdb_embedding_backend',
    'sagevdb_embedding_model', 'sagevdb_embedding_base_url', 'effective_embedding_endpoint',
    'sagevdb_embedding_query_instruction', 'sagevdb_embedding_batch_size',
    'knowledge_base_dir', 'knowledge_sagevdb_persistence_dir',
    'wiki_index_dir', 'knowledge_namespace',
    'neuromem_index_type', 'neuromem_embedding_model', 'neuromem_embedding_dim',
)


def projection(settings):
    return {
        name: str((settings.sagevdb_embedding_base_url or settings.llm_base_url)
                  if name == 'effective_embedding_endpoint' else getattr(settings, name))
        for name in FIELDS
    }


def differences(current, expected):
    return sorted(name for name in FIELDS if current.get(name) != expected.get(name))


def process_environment(pid: int, proc_root: Path = Path('/proc')):
    if pid <= 0:
        raise RuntimeError('Twin must be running before scheduled ingestion')
    process = proc_root / str(pid)
    command = (process / 'cmdline').read_bytes().split(b'\0')
    if b'sage_faculty_twin.api:app' not in command:
        raise RuntimeError('Refusing to compare an unrelated process')
    environment = {}
    for entry in (process / 'environ').read_bytes().split(b'\0'):
        if b'=' in entry:
            key, value = entry.split(b'=', 1)
            environment[key.decode()] = value.decode()
    return command[0].decode(), environment, process / 'cwd'


def main():
    if sys.argv[1:] == ['--projection']:
        from sage_faculty_twin.config import AppSettings
        print(json.dumps(projection(AppSettings())))
        return 0
    try:
        pid = int(subprocess.check_output(
            ['systemctl', '--user', 'show', 'sage-mate-app.service', '-p', 'MainPID', '--value'],
            text=True, timeout=10,
        ).strip())
        interpreter, environment, cwd = process_environment(pid)
        result = subprocess.run(
            [interpreter, str(Path(__file__).resolve()), '--projection'],
            env=environment, cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            raise RuntimeError('Cannot read running app retrieval settings')
        from sage_faculty_twin.config import AppSettings
        mismatch = differences(projection(AppSettings()), json.loads(result.stdout))
        if mismatch:
            print('Retrieval configuration mismatch: ' + ', '.join(mismatch), file=sys.stderr)
            return 1
        print('Retrieval configuration matches running Twin; safe to ingest')
        return 0
    except Exception:
        # Never emit subprocess stderr or validation errors containing settings.
        print('Cannot verify running Twin retrieval configuration; ingestion refused', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
