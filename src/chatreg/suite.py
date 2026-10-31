"""离线私有适配器和故障验证共用的套件入口。"""
from pathlib import Path

from .baseline import digest
from .cli import _parse_inputs, discover_files
from .reporting import build_report
from .runner import run_scenario


def scenario_records(path):
    return [{"relative_path": source, "sha256": digest(file.read_bytes())}
            for source, file in discover_files(Path(path))]


async def execute_suite(path, factory, adapter_id, *, reverse=False):
    loaded, errors = _parse_inputs(Path(path))
    if errors:
        return build_report([], errors, adapter_id)
    results = []
    for source, scenario in (reversed(loaded) if reverse else loaded):
        results.append(await run_scenario(scenario, factory, source))
    return build_report(results, [], adapter_id)
