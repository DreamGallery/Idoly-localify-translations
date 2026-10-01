#!/usr/bin/env python3
"""Read-only data preflight. Never import modules or scripts from contributions."""
import argparse
import csv
import json
from pathlib import Path

def check(root):
    total = 0
    for directory in ('story/ai', 'story/human', 'story/reviewed', 'records',
                      'master', 'notice', 'ui', 'legal', 'glossary', 'story-metadata'):
        base = root / directory
        for path in base.rglob('*'):
            if path.is_symlink(): raise ValueError(f'Symlink rejected: {path}')
            if not path.is_file(): continue
            if path.name != '.gitkeep' and path.suffix not in ('.json', '.csv', '.md'):
                raise ValueError(f'Unexpected contribution file: {path}')
            if path.stat().st_size > 128 * 1024 * 1024:
                raise ValueError(f'Oversized contribution file: {path}')
            if path.suffix == '.json': json.loads(path.read_text(encoding='utf-8'))
            if path.suffix == '.csv':
                with path.open(encoding='utf-8-sig', newline='') as stream:
                    reader = csv.DictReader(stream)
                    if reader.fieldnames != ['id', 'name', 'text', 'trans']:
                        raise ValueError(f'Invalid CSV columns: {path}')
                    for row in reader:
                        if None in row or None in row.values(): raise ValueError(f'Malformed CSV: {path}')
            total += 1
    return total
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, nargs='?', default=Path(__file__).resolve().parents[1])
    print(json.dumps({'validated_files': check(parser.parse_args().root)}))
