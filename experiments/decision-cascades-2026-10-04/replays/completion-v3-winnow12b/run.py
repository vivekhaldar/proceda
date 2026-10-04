"""Decision-only replay of the frozen 13 v3 fixtures against installed Winnow 12b."""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import shutil
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from proceda.config import DecisionBackendConfig
from proceda.decision import DecisionService

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
SOURCE = OUT.parent / 'completion-v3'
SERVICE = ROOT / 'src/proceda/decision/service.py'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    with (OUT / name).open('x') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')


def model_status(name):
    result = subprocess.run(['/Users/Vivek.Haldar/.local/bin/ollaya', 'ps'],
                            capture_output=True, text=True, timeout=15, check=False)
    (OUT / name).write_text(result.stdout + result.stderr)


async def main():
    fixtures = json.loads((SOURCE / 'fixtures.json').read_text())
    assert len(fixtures) == 13
    assert sum(f['expected_gate'] is True for f in fixtures) == 6
    backend = DecisionBackendConfig(
        name='winnow-12b-local', kind='ollaya', model='winnow:12b',
        base_url='http://127.0.0.1:11435', api_key_env='', max_input_chars=18000,
        timeout=90, input_cost_per_million=0,
    )
    service_hash = digest(SERVICE)
    shutil.copyfile(SOURCE / 'fixtures.json', OUT / 'fixtures.json')
    save('manifest.json', {
        'scope': 'Decision-only development replay, not end-to-end SOP or heldout evaluation.',
        'created_at': datetime.now(UTC).isoformat(), 'threshold': .97,
        'backend': dataclasses.asdict(backend), 'fixture_count': len(fixtures),
        'fixture_source': str((SOURCE / 'fixtures.json').relative_to(ROOT)),
        'fixtures_sha256': digest(SOURCE / 'fixtures.json'),
        'source_manifest_sha256': digest(SOURCE / 'manifest.json'),
        'source_fixture_manifest': json.loads((SOURCE / 'manifest.json').read_text()),
        'service_sha256': service_hash, 'script_sha256': digest(Path(__file__)),
        'labels_sent_to_models': False, 'parallelism': 1,
        'model_loading': 'First call includes any cold model load; subsequent calls use same server.',
    })
    model_status('ollaya-before.txt')
    started = time.monotonic()
    results = []
    for fixture in fixtures:
        service = DecisionService([backend], threshold=.97, audit_dir=OUT / fixture['id'])
        gate = await service.check_completion(step=fixture['step'], evidence=fixture['evidence'])
        row = {'fixture_id': fixture['id'], 'expected_gate': fixture['expected_gate'],
               'fixture_kind': fixture['fixture_kind'], 'value': gate.value,
               'reason': gate.reason, 'probabilities': gate.probabilities,
               'elapsed_seconds': gate.elapsed_seconds, 'details': gate.details}
        with (OUT / 'results.jsonl').open('a') as handle:
            handle.write(json.dumps(row) + '\n')
        results.append(row)
        print(json.dumps({k: row[k] for k in ('fixture_id','value','probabilities','elapsed_seconds')}),
              flush=True)
    durations = [r['elapsed_seconds'] for r in results]
    positives = [r for r in results if r['expected_gate'] is True]
    negatives = [r for r in results if r['expected_gate'] is False]
    summary = {
        'positive_accepted': sum(r['value'] is True for r in positives),
        'positive_total': len(positives),
        'negative_false_accepted': sum(r['value'] is True for r in negatives),
        'negative_rejected': sum(r['value'] is False for r in negatives),
        'negative_abstained': sum(r['value'] is None for r in negatives),
        'negative_total': len(negatives),
        'errors': sum(any(d['outcome'] == 'error' for d in r['details']) for r in results),
        'total_elapsed_seconds': time.monotonic()-started,
        'call_seconds_total': sum(durations), 'call_seconds_mean': statistics.mean(durations),
        'call_seconds_median': statistics.median(durations),
        'first_call_seconds': durations[0],
        'remaining_call_seconds_mean': statistics.mean(durations[1:]),
        'call_seconds_max': max(durations),
        'service_unchanged_during_replay': digest(SERVICE) == service_hash,
        'api_cost_usd': 0, 'local_compute_is_not_free': True,
    }
    model_status('ollaya-after.txt')
    save('summary.json', summary)
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
