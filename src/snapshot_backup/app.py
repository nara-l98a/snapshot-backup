"""Create and verify local, versioned directory snapshots."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ENV_DATA = "SNAPSHOT_BACKUP_DATA"


def state_path(value: str | None = None) -> Path:
    default = Path.home() / ".snapshot-backup.json"
    return Path(value or os.environ.get(ENV_DATA, default)).expanduser()


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "snapshots": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取数据文件 {path}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("snapshots", []), list):
        raise RuntimeError(f"数据文件结构无效：{path}")
    value.setdefault("version", 1)
    value.setdefault("snapshots", [])
    return value


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def ensure_outside(source: Path, output: Path) -> None:
    try:
        output.relative_to(source)
    except ValueError:
        return
    raise ValueError("拒绝：输出目录不能位于源目录内（含源目录本身）")


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def create(source: Path, output: Path, data: Path) -> dict:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"源目录不存在或不是目录：{source}")
    ensure_outside(source, output)
    state = load_state(data)
    output.mkdir(parents=True, exist_ok=True)
    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:10]
    destination = output / snapshot_id
    destination.mkdir()
    payload = destination / "data"
    payload.mkdir()
    files: list[dict] = []
    skipped: list[str] = []
    try:
        for root, directories, names in os.walk(source, followlinks=False):
            root_path = Path(root)
            for name in list(directories):
                candidate = root_path / name
                if candidate.is_symlink():
                    skipped.append(str(candidate.relative_to(source)))
                    directories.remove(name)
            for name in sorted(names):
                original = root_path / name
                relative = original.relative_to(source)
                if original.is_symlink() or not original.is_file():
                    skipped.append(str(relative))
                    continue
                target = payload / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, target)
                files.append({"path": str(relative), "sha256": digest(target), "size": target.stat().st_size})
        manifest = {
            "format": 1,
            "id": snapshot_id,
            "source": str(source),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
            "skipped_symlinks_or_nonregular": sorted(skipped),
        }
        atomic_json(destination / "manifest.json", manifest)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    state.setdefault("snapshots", []).append(
        {
            "id": snapshot_id,
            "path": str(destination),
            "source": str(source),
            "created_at": manifest["created_at"],
            "file_count": len(files),
        }
    )
    atomic_json(data, state)
    return manifest


def find_snapshot(value: str, data: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_dir() and (path / "manifest.json").is_file():
        return path.resolve()
    for item in load_state(data).get("snapshots", []):
        if isinstance(item, dict) and item.get("id") == value and isinstance(item.get("path"), str):
            return Path(item["path"]).expanduser().resolve()
    raise ValueError(f"找不到快照：{value}")


def verify(snapshot: Path) -> tuple[bool, list[str]]:
    snapshot = snapshot.expanduser().resolve()
    try:
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 manifest.json：{exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise ValueError("manifest.json 结构无效")
    payload = snapshot / "data"
    if payload.is_symlink() or not payload.is_dir():
        return False, ["缺失或无效：data/ 目录"]
    payload_root = payload.resolve()
    problems: list[str] = []
    expected_paths: set[str] = set()
    for item in manifest["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            problems.append("manifest 含无效文件记录")
            continue
        path_text = item["path"]
        if path_text in expected_paths:
            problems.append(f"manifest 含重复文件记录：{path_text}")
        expected_paths.add(path_text)
        relative = Path(path_text)
        if relative.is_absolute() or not relative.parts or any(part in (".", "..") for part in relative.parts):
            problems.append(f"路径越界：{item['path']}")
            continue
        candidate = payload
        unsafe = False
        for part in relative.parts:
            candidate = candidate / part
            if candidate.is_symlink():
                unsafe = True
                break
        if unsafe:
            problems.append(f"包含符号链接：{item['path']}")
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(payload_root)
        except FileNotFoundError:
            problems.append(f"缺失：{item['path']}")
            continue
        except (OSError, ValueError):
            problems.append(f"路径无效：{item['path']}")
            continue
        if not resolved.is_file():
            problems.append(f"缺失：{item['path']}")
            continue
        if resolved.stat().st_size != item.get("size") or digest(resolved) != item.get("sha256"):
            problems.append(f"校验失败：{item['path']}")
    for root, directories, names in os.walk(payload, followlinks=False):
        root_path = Path(root)
        for name in list(directories):
            candidate = root_path / name
            if candidate.is_symlink():
                directories.remove(name)
                relative = str(candidate.relative_to(payload))
                if relative not in expected_paths:
                    problems.append(f"未登记内容：{relative}")
        for name in names:
            relative = str((root_path / name).relative_to(payload))
            if relative not in expected_paths:
                problems.append(f"未登记内容：{relative}")
    return not problems, problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapshot-backup",
        description="创建并校验本地目录版本快照（不删除任何源文件或旧快照）",
    )
    parser.add_argument("--data", "--db", dest="data", help=f"状态 JSON 路径（也可用 {ENV_DATA}）")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create", help="创建快照")
    create_parser.add_argument("source")
    create_parser.add_argument("--output", "-o", required=True)
    list_parser = subparsers.add_parser("list", help="列出已登记快照")
    list_parser.add_argument("--json", action="store_true")
    verify_parser = subparsers.add_parser("verify", help="校验快照完整性")
    verify_parser.add_argument("snapshot")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data = state_path(args.data)
    try:
        if args.command == "create":
            manifest = create(Path(args.source), Path(args.output), data)
            skipped = len(manifest["skipped_symlinks_or_nonregular"])
            print(f"已创建快照：{manifest['id']}（{len(manifest['files'])} 个文件，跳过 {skipped} 个链接/非普通文件）")
        elif args.command == "list":
            rows = load_state(data).get("snapshots", [])
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                print("ID\t文件数\t源目录\t路径")
                for item in rows:
                    print(f"{item['id']}\t{item['file_count']}\t{item['source']}\t{item['path']}")
        else:
            valid, problems = verify(find_snapshot(args.snapshot, data))
            print("完整性校验通过" if valid else "完整性校验失败")
            for problem in problems:
                print(problem, file=sys.stderr)
            return 0 if valid else 1
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
