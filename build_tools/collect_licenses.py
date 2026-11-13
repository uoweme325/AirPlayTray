"""Collect license and notice files for pinned runtime dependencies."""

import argparse
import importlib.metadata as md
from pathlib import Path
import shutil


def load_locked_names(lock_file: Path):
    names = set()
    for line in lock_file.read_text(encoding="utf-8-sig").splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#") or cleaned.startswith("-r "):
            continue
        pkg = cleaned.split("==", 1)[0].split(";", 1)[0].strip()
        if pkg:
            names.add(pkg.lower().replace("_", "-"))
    return names


def is_license_relpath(relpath: Path) -> bool:
    parts_lower = [part.lower() for part in relpath.parts]
    if "licenses" in parts_lower:
        return True
    stem = relpath.name.lower()
    return any(token in stem for token in ("license", "copying", "notice"))


def _relative_license_dest(relpath: Path) -> Path:
    parts = relpath.parts
    if parts and parts[0].lower().endswith(".dist-info"):
        trimmed = Path(*parts[1:])
        return trimmed if trimmed.parts else Path(relpath.name)
    return relpath


def collect_licenses(lock_file: Path, output_dir: Path) -> int:
    target_names = load_locked_names(lock_file)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    seen_dests = set()
    matched = set()
    for dist in sorted(md.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        raw_name = dist.metadata["Name"] or ""
        norm_name = raw_name.lower().replace("_", "-")
        if norm_name not in target_names:
            continue
        matched.add(norm_name)
        pkg_dir = output_dir / ("%s-%s" % (raw_name, dist.version))
        lic_files = [rel for rel in (dist.files or []) if is_license_relpath(Path(str(rel)))]
        if lic_files:
            for rel in lic_files:
                rel_path = Path(str(rel))
                src = Path(dist.locate_file(rel))
                if not src.is_file():
                    continue
                dest = pkg_dir / _relative_license_dest(rel_path)
                if dest in seen_dests:
                    raise RuntimeError("License destination collision: %s -> %s" % (src, dest))
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                seen_dests.add(dest)
        else:
            lic_text = dist.metadata.get("License") or dist.metadata.get("License-Expression") or ""
            if lic_text:
                dest = pkg_dir / "METADATA-LICENSE.txt"
                if dest in seen_dests:
                    raise RuntimeError("License destination collision: %s" % dest)
                pkg_dir.mkdir(parents=True, exist_ok=True)
                dest.write_text(lic_text, encoding="utf-8")
                seen_dests.add(dest)

    missing = sorted(target_names - matched)
    if missing:
        raise RuntimeError("Missing installed distributions for license collection: %s" % ", ".join(missing))
    return len(seen_dests)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bundle third-party dependency license files.")
    parser.add_argument("--lock", default="requirements-lock.txt")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    count = collect_licenses(Path(args.lock), Path(args.output))
    print("Collected %d unique license files into %s" % (count, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
