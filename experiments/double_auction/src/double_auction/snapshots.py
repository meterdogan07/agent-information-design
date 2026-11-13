"""Automatic, write-once provenance for locally executed auction studies."""

import hashlib
import json
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def capture_sources(paths, repository):
    repository = Path(repository).resolve()
    return {str(Path(path).resolve().relative_to(repository)): Path(path).read_bytes()
            for path in paths}


def market_bytes(instances):
    return ''.join(json.dumps(row, sort_keys=True) + '\n' for row in instances).encode()


def save_snapshot(directory, config, sources, markets):
    """Verify existing snapshots, or finish a snapshot interrupted before API calls."""
    directory = Path(directory)
    if {name: digest(data) for name, data in sources.items()} != config['source_sha256']:
        raise ValueError('source differs from saved run')
    if digest(markets) != config['markets_sha256']:
        raise ValueError('markets differ from saved run')
    for path, data in [(directory / 'markets.jsonl', markets), *[
        (directory / 'source_snapshot' / name, data) for name, data in sources.items()
    ]]:
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError(f'saved snapshot differs: {path}')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as handle:
                handle.write(data)


def verify_snapshot(directory, config):
    directory = Path(directory)
    for name, expected in config['source_sha256'].items():
        path = (directory / 'source_snapshot' / name).resolve()
        if not path.is_relative_to((directory / 'source_snapshot').resolve()):
            raise ValueError('unsafe source snapshot path')
        if digest(path.read_bytes()) != expected:
            raise ValueError(f'source snapshot checksum mismatch: {name}')
    data = (directory / 'markets.jsonl').read_bytes()
    if digest(data) != config['markets_sha256']:
        raise ValueError('market snapshot checksum mismatch')
