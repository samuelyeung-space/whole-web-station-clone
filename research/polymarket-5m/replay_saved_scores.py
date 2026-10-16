"""Join already-frozen local scores to exact registered development tapes.

No model fitting and no dropping decisions without scores. Inputs must match the
original model registration; every score model has an earlier-day label cutoff.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path
from replay_saved_features_admission import admit_saved_features

DAY_MS = 86400000


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    with gzip.open(path, 'rt') as f:
        for line in f:
            yield json.loads(line)


def prepare(paths, scores_path, registration_path, audit_path, output, expected_scores_sha256=None):
    paths = list(paths)
    scores, admission = admit_saved_features(paths, scores_path, registration_path, audit_path,
        expected_scores_sha256=expected_scores_sha256)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    provenance = {'scores_sha256': sha(scores_path), 'model_registration_sha256': sha(registration_path),
                  'model_audit_sha256': sha(audit_path), 'adapter_sha256': sha(__file__),
                  'admission_code_sha256': admission['admission_code_sha256'],
                  'model_fitting': False, 'input_data_role': 'already_consumed_development',
                  'formal_oos_result': None,
                  'missing_score_policy': 'retain decision unchanged; required model feature is missing',
                  'training_policy': 'original saved scores; strictly earlier UTC-day labels per saved audit'}
    result = {'schema': 'saved-score-join-v2', 'provenance': provenance, 'admission': admission, 'inputs': []}
    for path in paths:
        path = Path(path)
        dest = output / path.name
        if dest.exists():
            raise ValueError('output exists; verify existing manifest instead of overwriting')
        pending = dest.with_suffix(dest.suffix + '.pending')
        counts = Counter()
        with gzip.open(pending, 'wt', compresslevel=1) as f:
            for e in read(path):
                counts[e['type']] += 1
                if e['type'] == 'dataset':
                    if e.get('split') != 'development':
                        raise ValueError('only consumed development inputs admitted')
                    e['saved_score_join'] = {**provenance, 'original_tape_sha256': sha(path)}
                    e['dataset_id'] += ':saved_scores'
                elif e['type'] == 'decision':
                    s = scores.get((e['market_id'], e['recv_ms']))
                    if s is None:
                        counts['decisions_without_score'] += 1
                    else:
                        overlap = set(s).intersection(e['features'])
                        if any(e['features'][k] != s[k] for k in overlap):
                            raise ValueError('saved score overwrites an existing different feature')
                        e['features'].update(s)
                        e['feature_available_ms'].update({k: e['recv_ms'] for k in s})
                        counts['decisions_with_score'] += 1
                f.write(json.dumps(e, separators=(',', ':'), allow_nan=False) + '\n')
        # Close gzip before validating all records and publishing.
        if sum(1 for _ in read(pending)) != sum(counts[k] for k in ('dataset', 'market', 'constraint', 'book', 'decision', 'outcome')):
            raise ValueError('record conservation failure')
        pending.replace(dest)
        result['inputs'].append({'input_path': str(path), 'input_sha256': sha(path),
                                'output_path': str(dest), 'output_sha256': sha(dest), 'counts': dict(counts)})
    (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', nargs='+', required=True)
    for name in ('scores', 'registration', 'audit', 'output'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--expected-scores-sha256', help='pin the original saved-score artifact')
    a = p.parse_args()
    print(json.dumps(prepare(a.input, a.scores, a.registration, a.audit, a.output, a.expected_scores_sha256)))


if __name__ == '__main__':
    main()
