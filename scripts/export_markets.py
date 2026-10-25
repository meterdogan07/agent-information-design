"""Export or verify deterministic market data, without contacting model APIs."""
from pathlib import Path
import argparse
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'experiments'))
for directory in ('multiresource_trade', 'double_auction/src'):
    sys.path.insert(0, str(ROOT / 'experiments' / directory))
from src.trading.markets import generate_markets as trade_markets
from src.trading.markets import split_market_record
from single_good_bargaining.src.design import generate_profitable_mirrored_markets
from double_auction.market import gen_instances


def generated_panels():
    yield 'multiresource_trade/data/main_markets.jsonl', trade_markets(panel='extraction_region_grid'), 'Two resources; 30 markets per extraction-opportunity group; no random seed.'
    focused = trade_markets(panel='responder_extraction_crossed')
    yield 'multiresource_trade/data/mediator_markets.jsonl', focused, 'Matched 30-market B-higher panel for direct and mediated trade.'
    ids = (
        'a-high_gap_1_11__b-near_flat_5_7__q2_1__xy',
        'a-high_gap_1_11__b-near_flat_4_8__q2_1__yx',
        'a-high_gap_2_10__b-near_flat_3_9__q2_1__yx',
        'a-high_gap_1_11__b-near_flat_5_7__q3_1__yx',
        'a-high_gap_1_11__b-near_flat_4_8__q3_1__xy',
        'a-high_gap_2_10__b-near_flat_3_9__q3_1__xy',
    )
    selected = [m for m in focused if m['market_id'] in ids]
    assert len(selected) == 6
    yield 'multiresource_trade/data/dialogue_markets.jsonl', selected, 'Six fixed markets reused at message limits 2, 4, 6, 8, and 12.'
    for resources in (2, 6, 10):
        yield f'multiresource_trade/data/resources_{resources}.jsonl', trade_markets(panel='heterogeneous_complexity_scaling', resource_count=resources), 'Six utility constructions, each with two resource-name versions; nested resource expansion.'
    yield 'single_good_bargaining/data/main_markets.jsonl', generate_profitable_mirrored_markets(30, 20260824), 'Seed 20260824; 15 cost-value pairs, five per surplus group, each under both starting roles.'
    yield 'double_auction/data/main_markets.jsonl', gen_instances(30, seed=97617, market_design='margin_sweep'), 'Seed 97617; 3 buyers and 3 sellers; 10 markets per margin group; midpoint pricing.'
    yield 'double_auction/data/placement_markets.jsonl', gen_instances(15, seed=97731, market_design='three_family'), 'Seed 97731; five markets per efficient-quantity group; midpoint pricing.'
    for size in (3, 6, 10):
        yield f'double_auction/data/size_{size}.jsonl', gen_instances(15, size, size, seed=97731, market_design='size_scaling'), 'Seed 97731; nested market-size study; uniform-price clearing, not main-study midpoint pricing.'


def panels():
    for relative, markets, description in generated_panels():
        relative = 'experiments/' + relative
        if relative.startswith('experiments/multiresource_trade/data/'):
            records, metadata = zip(*(split_market_record(market) for market in markets))
            yield relative, list(records), description
            companion = Path(relative).parent / 'metadata' / Path(relative).name
            yield str(companion), list(metadata), f'Construction metadata and evaluator calculations for {relative}; join by market_id.'
        else:
            yield relative, markets, description


def canonical(rows):
    # Preserve schema order so inventories and utilities precede grouping fields.
    return ''.join(json.dumps(row) + '\n' for row in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify snapshots without writing files.')
    args = parser.parse_args()
    manifest = []
    for relative, generated, description in panels():
        path = ROOT / relative
        if path.exists():
            saved = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            # Ordering is not part of the market definition; retain the main-study order.
            assert sorted(json.dumps(r, sort_keys=True) for r in saved) == sorted(json.dumps(r, sort_keys=True) for r in generated), f'Market mismatch: {relative}'
        elif args.check:
            raise FileNotFoundError(relative)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(canonical(generated))
        manifest.append({'path': relative, 'markets': len(generated), 'description': description,
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        print(f'{len(generated):3} markets verified: {relative}')
    target = ROOT / 'data' / 'MARKETS.json'
    if args.check:
        assert json.loads(target.read_text()) == manifest, 'Market manifest mismatch'
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
