import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


def _hash(path: Path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").digest()


def configure_results(store):
    previous = Path(store.setting("result_directory", str(store.root / "results")))
    configured = os.getenv("ZAOWU_PROVIDER_RESULT_DIR", "")
    target = Path(configured) if configured else previous
    if configured and not target.is_absolute():
        raise ValueError("生成结果目录必须是绝对路径")
    if target == previous:
        return target
    source = previous.resolve()
    destination = target.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("新旧结果目录不能相互包含或通过链接指向同一位置")
    target.mkdir(parents=True, exist_ok=True)
    if any(item.is_symlink() for item in (target, *target.parents)):
        raise ValueError("生成结果目录不能包含链接")
    if previous.exists():
        if previous.is_symlink():
            raise ValueError("生成结果迁移源不能是链接")
        for item in previous.iterdir():
            if item.is_symlink() or not item.is_file():
                raise ValueError("生成结果迁移源包含非普通文件")
            output = target / item.name
            if output.exists():
                if output.is_symlink() or not output.is_file() or _hash(item) != _hash(output):
                    raise ValueError("生成结果目标目录存在不同内容的同名文件")
                continue
            with tempfile.NamedTemporaryFile(dir=target, prefix=".zaowu-copy-", delete=False) as stream:
                temporary = Path(stream.name)
            try:
                shutil.copyfile(item, temporary)
                if _hash(item) != _hash(temporary):
                    raise ValueError("生成结果迁移校验失败")
                # Exclusive creation preserves pre-existing destination files.
                os.link(temporary, output)
                if _hash(item) != _hash(output):
                    raise ValueError("生成结果迁移校验失败")
            finally:
                temporary.unlink(missing_ok=True)
    updates = []
    for row in store.rows("SELECT id,result_path FROM tasks WHERE result_path IS NOT NULL AND result_path != ''"):
        result = Path(row["result_path"])
        if result.parent.resolve() != source:
            raise ValueError("任务结果路径超出原结果目录，已停止迁移")
        output = target / result.name
        if not output.is_file() or output.is_symlink():
            raise ValueError("任务结果文件缺失，已停止迁移")
        updates.append((str(output), row["id"]))
    with store.transaction() as db:
        db.executemany("UPDATE tasks SET result_path = ? WHERE id = ?", updates)
        db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", ("result_directory", json.dumps(str(target))))
    return target
