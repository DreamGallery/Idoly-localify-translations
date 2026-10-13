"""Print allowlisted release diagnostics; never print private log excerpts."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

PATTERNS = {
    'notice_numbers_changed': 'notice numbers, dates or amounts changed',
    'invalid_response_json': 'Model response is not the required translation JSON',
    'empty_model_text': 'Model did not return text',
    'output_token_limit': 'Model used the full output token limit before finishing',
    'connection_failed': 'Translation API connection failed',
    'stale_review_list': 'Notice review list is stale or invalid',
    'review_excludes_pending': 'Notice review list excludes ',
    'invalid_cached_result': 'invalid notice result',
    'source_changed': 'Notice source changed while translating',
    'placeholder_mismatch': 'placeholder',
    'missing_translation_ids': 'missing translation',
    'protected_markers_changed': 'Protected notice markers changed, duplicated or reordered',
    'collector_locked': 'Collector account is locked;',
    'collector_missing': 'No collector account;',
    'collector_invalid': 'Invalid dedicated collector account cache',
    'collector_creation_uncertain': 'Previous account creation has uncertain outcome;',
    'collector_path_invalid': 'Collector account must stay in ignored Toolkit/cache',
    'firebase_refresh_transport': 'Firebase refresh transport failed;',
    'firebase_tokens_missing': 'Firebase response is missing tokens',
    'firebase_expiry_invalid': 'Firebase response has invalid expiry',
    'official_seed_transport': 'Official seed transport failed',
    'official_seed_rejected': 'Official seed request failed',
    'official_application_error': 'Official API returned an application error;',
    'official_authentication_incomplete': 'Official authentication requires additional handling;',
    'official_master_version_missing': 'Official Master.Get did not return a version',
    'official_date_changed': 'Official Notice API returned DateChanged (1003);',
    'official_recovery_exhausted': 'Official notice recovery limit reached',
}
STAGES = {'translate-notice', 'translate-master', 'translate-story', 'notice-pages',
          'notice-index', 'master-fetch', 'master-export', 'master-tag', 'octo', 'story-catalog',
          'translate-notice-retry-1', 'translate-notice-retry-2'}


def classify(text):
    counts = {key: text.lower().count(value.lower()) for key, value in PATTERNS.items()
              if value.lower() in text.lower()}
    counts.update(Counter('http_' + code for code in re.findall(
        r'Translation API returned HTTP ([1-5][0-9]{2})\b', text)))
    counts.update(Counter('firebase_' + operation + '_http_' + code
                          for operation, code in re.findall(
        r'Firebase (refresh|signup) rejected \(HTTP ([1-5][0-9]{2})\)', text)))
    collector_errors = re.findall(
        r'Notice collection failed \((_InactiveRpcError|_MultiThreadedRendezvous|'
        r'ValueError|RuntimeError|TimeoutError|TypeError|KeyError|FileNotFoundError|'
        r'PermissionError|ModuleNotFoundError|JSONDecodeError)\);', text)
    counts.update({'collector_' + key: value for key, value in Counter(collector_errors).items()})
    rpc_errors = re.findall(
        r'Official RPC failed \(status (OK|CANCELLED|UNKNOWN|INVALID_ARGUMENT|'
        r'DEADLINE_EXCEEDED|NOT_FOUND|ALREADY_EXISTS|PERMISSION_DENIED|RESOURCE_EXHAUSTED|'
        r'FAILED_PRECONDITION|ABORTED|OUT_OF_RANGE|UNIMPLEMENTED|INTERNAL|UNAVAILABLE|'
        r'DATA_LOSS|UNAUTHENTICATED), application codes (none|[0-9]{1,5}(?:,[0-9]{1,5})*)\)', text)
    counts.update(Counter('official_rpc_' + status for status, _ in rpc_errors))
    counts.update(Counter('official_app_' + code for _, codes in rpc_errors
                          for code in codes.split(',') if code != 'none'))
    batches = re.findall(r'Notice batch failed \((ValueError|RuntimeError|TimeoutError|TypeError|KeyError)\)', text)
    counts.update({'batch_' + key: value for key, value in Counter(batches).items()})
    summary = re.findall(r'Notice: (\d+) new strings, (\d+) JSON values updated, (\d+)', text)
    summary += re.findall(r'Notice protected retry: (\d+) new, (\d+) applied, (\d+) pending', text)
    return {'signals': counts, 'translation_counts': list(map(int, summary[-1])) if summary else None}


def inspect(config_path):
    config = json.loads(Path(config_path).read_text())
    root = Path(config['work_dir'])
    summary_path = root / 'refresh-summary.json'
    if not summary_path.is_file():
        return {'status': 'no_previous_refresh_summary'}
    summary = json.loads(summary_path.read_text())
    stage = summary.get('stage')
    result = {'stage': stage if stage in STAGES else 'other',
              'complete': summary.get('complete') is True}
    if stage in STAGES:
        path = root / 'refresh' / (stage + '.log')
        if path.is_file():
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 2 * 1024 * 1024))
                result.update(classify(stream.read().decode('utf-8', errors='replace')))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.config), ensure_ascii=False))
    except Exception:
        print('{"status":"diagnostic_unavailable"}')
