#!/usr/bin/env python3
"""Sync Obsidian notes flagged with `share: true` in frontmatter to a GitHub repo."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

MANIFEST_NAME = ".obsidian_share_manifest.json"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
WIKI_EMBED_RE = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")


def load_config(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"설정 파일을 찾을 수 없습니다: {path}")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("vault_path", "repo_path"):
        if key not in cfg:
            sys.exit(f"설정 파일에 '{key}' 값이 필요합니다.")
    cfg.setdefault("share_key", "share")
    cfg.setdefault("flatten", False)
    cfg.setdefault("target_subdir", "")
    cfg.setdefault("commit_message", "Update shared notes ({count} changed)")
    cfg.setdefault("auto_push", True)
    cfg.setdefault("branch", None)
    cfg["vault_path"] = Path(cfg["vault_path"]).expanduser().resolve()
    cfg["repo_path"] = Path(cfg["repo_path"]).expanduser().resolve()
    return cfg


def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            block = "\n".join(lines[1:i])
            try:
                data = yaml.safe_load(block)
            except yaml.YAMLError:
                return {}
            return data if isinstance(data, dict) else {}
    return {}


def is_shared(frontmatter: dict, share_key: str) -> bool:
    value = frontmatter.get(share_key)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def resolve_embedded_images(text: str, vault_path: Path, md_file: Path) -> list:
    """md 파일 본문에서 ![[이미지]] / ![alt](경로) 참조를 찾아 vault 기준 상대경로로 해석."""
    refs = [m.group(1).strip() for m in WIKI_EMBED_RE.finditer(text)]
    for m in MD_IMAGE_RE.finditer(text):
        path = m.group(1).strip()
        if not path.startswith(("http://", "https://")):
            refs.append(path)

    resolved = []
    seen = set()
    for ref in refs:
        ref = ref.split("#", 1)[0].strip()
        if not ref:
            continue
        ext = Path(ref).suffix.lower()
        if ext and ext not in IMAGE_EXTS:
            continue

        candidate = None
        ref_path = Path(ref)
        note_relative = md_file.parent / ref_path
        vault_relative = vault_path / ref_path
        if note_relative.is_file():
            candidate = note_relative
        elif vault_relative.is_file():
            candidate = vault_relative
        else:
            matches = list(vault_path.rglob(ref_path.name))
            if matches:
                candidate = matches[0]

        if candidate is None:
            continue
        rel = candidate.resolve().relative_to(vault_path.resolve())
        if rel not in seen:
            seen.add(rel)
            resolved.append(rel)
    return resolved


def scan_vault(cfg: dict) -> dict:
    """Return {vault_relpath(str): repo_relpath(str)} for every note (and its
    embedded images) to share."""
    vault_path = cfg["vault_path"]
    target_subdir = Path(cfg["target_subdir"]) if cfg["target_subdir"] else Path(".")
    flatten = cfg["flatten"]

    selected = {}
    seen_flat_names = {}

    def add(rel: Path):
        if str(rel) in selected:
            return
        if flatten:
            repo_rel = target_subdir / rel.name
            if rel.name in seen_flat_names and seen_flat_names[rel.name] != rel:
                sys.exit(
                    f"flatten=true 인데 파일명이 중복됩니다: '{rel.name}' "
                    f"({seen_flat_names[rel.name]} vs {rel}). 폴더 구조를 유지하거나 "
                    f"파일명을 바꿔주세요."
                )
            seen_flat_names[rel.name] = rel
        else:
            repo_rel = target_subdir / rel
        selected[str(rel)] = str(repo_rel)

    shared_notes = []
    for md_file in vault_path.rglob("*.md"):
        text = md_file.read_text(encoding="utf-8", errors="ignore")
        fm = parse_frontmatter(text)
        if not is_shared(fm, cfg["share_key"]):
            continue
        rel = md_file.relative_to(vault_path)
        add(rel)
        shared_notes.append((md_file, text))

    for md_file, text in shared_notes:
        for img_rel in resolve_embedded_images(text, vault_path, md_file):
            add(img_rel)

    return selected


def file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def sync(cfg: dict, dry_run: bool) -> int:
    vault_path = cfg["vault_path"]
    repo_path = cfg["repo_path"]
    if not repo_path.exists():
        sys.exit(f"repo_path가 존재하지 않습니다 (먼저 git clone 해두세요): {repo_path}")

    manifest_path = repo_path / MANIFEST_NAME
    old_manifest = {}
    if manifest_path.exists():
        old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    selected = scan_vault(cfg)

    changed = 0

    # Copy new/changed files, and relocate files whose target path changed.
    for vault_rel, repo_rel in selected.items():
        src = vault_path / vault_rel
        dst = repo_path / repo_rel

        old_repo_rel = old_manifest.get(vault_rel)
        if old_repo_rel and old_repo_rel != repo_rel:
            old_dst = repo_path / old_repo_rel
            print(f"[이동] {old_repo_rel} -> {repo_rel}")
            if not dry_run and old_dst.exists():
                old_dst.unlink()
                changed += 1

        needs_copy = not dst.exists() or file_hash(src) != file_hash(dst)
        if needs_copy:
            print(f"[복사] {vault_rel} -> {repo_rel}")
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            changed += 1

    # Remove files that are no longer shared or were deleted from the vault.
    for vault_rel, repo_rel in old_manifest.items():
        if vault_rel in selected:
            continue
        target = repo_path / repo_rel
        if target.exists():
            print(f"[삭제] {repo_rel}")
            if not dry_run:
                target.unlink()
            changed += 1

    if dry_run:
        print(f"\n(dry-run) 변경 예정 파일 수: {changed}")
        return changed

    if not manifest_path.exists() or old_manifest != selected:
        manifest_path.write_text(
            json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return changed


def pull_into_vault(cfg: dict) -> int:
    """레포(manifest에 기록된 파일들)를 vault_path로 복사해온다. 충돌 시 레포 내용으로 덮어씀."""
    repo_path = cfg["repo_path"]
    vault_path = cfg["vault_path"]
    manifest_path = repo_path / MANIFEST_NAME
    if not manifest_path.exists():
        print("레포에 공유 매니페스트가 없어 가져올 파일이 없습니다.")
        return 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed = 0
    for vault_rel, repo_rel in manifest.items():
        src = repo_path / repo_rel
        dst = vault_path / vault_rel
        if not src.exists():
            print(f"[건너뜀] 레포에 없음: {repo_rel}")
            continue
        if not dst.exists() or file_hash(src) != file_hash(dst):
            print(f"[가져오기] {repo_rel} -> {vault_rel}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            changed += 1

    if changed == 0:
        print("vault가 이미 최신 상태입니다.")
    return changed


def _run_git_raw(repo_path: Path, args: list) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        sys.exit(
            "git 실행 파일을 찾을 수 없습니다. Git이 설치되어 있고 PATH에 등록되어 "
            "있는지 확인하세요 (Windows: https://git-scm.com/download/win 에서 설치)."
        )


def run_git(repo_path: Path, args: list) -> str:
    result = _run_git_raw(repo_path, args)
    if result.returncode != 0:
        sys.exit(f"git {' '.join(args)} 실패:\n{result.stderr}")
    return result.stdout.strip()


def branch_exists_locally(repo_path: Path, branch: str) -> bool:
    result = _run_git_raw(
        repo_path, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]
    )
    return result.returncode == 0


def remote_branch_exists(repo_path: Path, branch: str) -> bool:
    out = run_git(repo_path, ["ls-remote", "--heads", "origin", branch])
    return bool(out.strip())


def ensure_branch(repo_path: Path, branch: str) -> None:
    current = run_git(repo_path, ["symbolic-ref", "--short", "HEAD"])
    if current == branch:
        return

    status = run_git(repo_path, ["status", "--porcelain"])
    if status:
        sys.exit(
            f"'{repo_path}'에 커밋되지 않은 변경 사항이 있어 브랜치를 "
            f"'{branch}'(으)로 전환할 수 없습니다. 먼저 정리하거나 커밋해주세요."
        )

    if branch_exists_locally(repo_path, branch):
        run_git(repo_path, ["checkout", branch])
    elif remote_branch_exists(repo_path, branch):
        run_git(repo_path, ["fetch", "origin", branch])
        run_git(repo_path, ["checkout", "-b", branch, f"origin/{branch}"])
    else:
        print(f"'{branch}' 브랜치가 없어 현재 커밋에서 새로 생성합니다.")
        run_git(repo_path, ["checkout", "-b", branch])

    print(f"브랜치 '{branch}'(으)로 전환 완료.")


def current_branch(repo_path: Path) -> str:
    return run_git(repo_path, ["symbolic-ref", "--short", "HEAD"])


def list_branches(repo_path: Path, fetch: bool = True) -> list:
    """repo_path의 로컬 + 원격(origin) 브랜치 이름 목록 (중복 제거, 정렬)."""
    if fetch:
        _run_git_raw(repo_path, ["fetch", "origin", "--prune"])
    local = run_git(repo_path, ["branch", "--format=%(refname:short)"]).splitlines()
    remote_raw = run_git(
        repo_path, ["branch", "-r", "--format=%(refname:short)"]
    ).splitlines()
    remote = [
        b.split("/", 1)[1]
        for b in remote_raw
        if "/" in b and not b.endswith("HEAD")
    ]
    return sorted({b for b in local + remote if b})


def pull_latest(repo_path: Path) -> None:
    has_upstream = _run_git_raw(
        repo_path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
    ).returncode == 0
    if not has_upstream:
        print("원격 추적 브랜치가 없어 pull을 건너뜁니다.")
        return
    run_git(repo_path, ["pull", "--ff-only"])
    print("pull 완료.")


def push_branch(repo_path: Path, branch: str) -> None:
    if branch:
        has_upstream = _run_git_raw(
            repo_path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
        ).returncode == 0
        if has_upstream:
            run_git(repo_path, ["push"])
        else:
            run_git(repo_path, ["push", "-u", "origin", branch])
    else:
        run_git(repo_path, ["push"])


def cli_confirm(status: str) -> bool:
    print("\n변경된 파일:")
    print(status)
    answer = input("commit 및 push 할까요? [y/N] ").strip().lower()
    return answer == "y"


def commit_and_push(cfg: dict, changed: int, confirm) -> None:
    """confirm: Callable[[str], bool] — git status 출력을 받아 진행 여부를 결정."""
    repo_path = cfg["repo_path"]
    status = run_git(repo_path, ["status", "--porcelain"])
    if not status:
        print("변경 사항 없음 (git status 깨끗함).")
        return

    if not confirm(status):
        print("커밋을 건너뜁니다.")
        return

    run_git(repo_path, ["add", "-A"])
    message = cfg["commit_message"].format(count=changed)
    run_git(repo_path, ["commit", "-m", message])
    print(f"커밋 완료: {message}")

    if cfg["auto_push"]:
        push_branch(repo_path, cfg["branch"])
        print("push 완료.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-c", "--config", default="config.yaml", help="설정 파일 경로 (기본값: config.yaml)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="실제로 복사/git 작업 없이 계획만 출력"
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="commit/push 확인 프롬프트 생략"
    )
    parser.add_argument(
        "--pull",
        action="store_true",
        help="업로드 대신, 레포 내용을 vault로 가져오기 (브랜치 체크아웃+pull 후 vault에 복사)",
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    if cfg["branch"]:
        ensure_branch(cfg["repo_path"], cfg["branch"])

    if args.pull:
        pull_latest(cfg["repo_path"])
        changed = pull_into_vault(cfg)
        if changed:
            print(f"{changed}개 파일을 vault로 가져왔습니다.")
        return

    changed = sync(cfg, dry_run=args.dry_run)

    if args.dry_run:
        return

    if changed == 0:
        print("공유 대상 변경 없음.")

    commit_and_push(cfg, changed, confirm=(lambda status: True) if args.yes else cli_confirm)


if __name__ == "__main__":
    main()
