#!/usr/bin/env python3
"""obsidian_share의 간단한 GUI.

- 브랜치 목록 드롭다운
- 선택한 브랜치를 로컬로 불러오기(체크아웃 + pull + vault로 복사) 버튼
- vault의 공유 대상 md 파일을 GitHub 레포에 올리기(commit + push) 버튼
"""

import contextlib
import io
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from obsidian_share import (
    commit_and_push,
    current_branch,
    ensure_branch,
    list_branches,
    load_config,
    pull_into_vault,
    pull_latest,
    save_config,
    sync,
)

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.yaml"


class ObsidianShareGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Obsidian Share")
        self.cfg = None
        self._busy_widgets = []
        self._callback_queue = queue.Queue()

        self._build_widgets()
        self.root.after(50, self._poll_callback_queue)
        if DEFAULT_CONFIG.exists():
            self.config_path_var.set(str(DEFAULT_CONFIG))
            self.on_load_config()

    def _poll_callback_queue(self):
        """메인 스레드에서만 실행됨. 워커 스레드는 여기로 콜백을 넘기기만 한다.

        Tkinter(특히 macOS의 Tcl/Tk)는 메인 스레드가 아닌 곳에서 위젯을 건드리면
        조용히 크래시할 수 있어서, 워커 스레드가 root.after()를 직접 호출하지
        않도록 큐를 거친다."""
        while True:
            try:
                callback = self._callback_queue.get_nowait()
            except queue.Empty:
                break
            callback()
        self.root.after(50, self._poll_callback_queue)

    # ---------- UI ----------

    def _build_widgets(self):
        pad = {"padx": 8, "pady": 4}

        config_frame = ttk.Frame(self.root)
        config_frame.pack(fill="x", **pad)
        ttk.Label(config_frame, text="설정 파일").pack(side="left")
        self.config_path_var = tk.StringVar()
        ttk.Entry(config_frame, textvariable=self.config_path_var, width=50).pack(
            side="left", padx=4, fill="x", expand=True
        )
        ttk.Button(config_frame, text="찾아보기", command=self.on_browse_config).pack(
            side="left"
        )
        ttk.Button(config_frame, text="불러오기", command=self.on_load_config).pack(
            side="left", padx=(4, 0)
        )

        vault_frame = ttk.Frame(self.root)
        vault_frame.pack(fill="x", **pad)
        ttk.Label(vault_frame, text="Vault 경로").pack(side="left")
        self.vault_path_var = tk.StringVar()
        ttk.Entry(vault_frame, textvariable=self.vault_path_var, width=50).pack(
            side="left", padx=4, fill="x", expand=True
        )
        ttk.Button(
            vault_frame, text="찾아보기", command=self.on_browse_vault
        ).pack(side="left")

        repo_frame = ttk.Frame(self.root)
        repo_frame.pack(fill="x", **pad)
        ttk.Label(repo_frame, text="Repo 경로").pack(side="left")
        self.repo_path_var = tk.StringVar()
        ttk.Entry(repo_frame, textvariable=self.repo_path_var, width=50).pack(
            side="left", padx=4, fill="x", expand=True
        )
        ttk.Button(
            repo_frame, text="찾아보기", command=self.on_browse_repo
        ).pack(side="left")
        ttk.Button(
            repo_frame, text="설정 저장", command=self.on_save_config
        ).pack(side="left", padx=(4, 0))

        branch_frame = ttk.Frame(self.root)
        branch_frame.pack(fill="x", **pad)
        ttk.Label(branch_frame, text="브랜치").pack(side="left")
        self.branch_var = tk.StringVar()
        self.branch_combo = ttk.Combobox(
            branch_frame, textvariable=self.branch_var, width=30
        )
        self.branch_combo.pack(side="left", padx=4)
        self._busy_widgets.append(self.branch_combo)

        action_frame = ttk.Frame(self.root)
        action_frame.pack(fill="x", **pad)
        self.load_btn = ttk.Button(
            action_frame, text="선택한 브랜치 불러오기", command=self.on_load_branch
        )
        self.load_btn.pack(side="left")
        self._busy_widgets.append(self.load_btn)

        self.upload_btn = ttk.Button(
            action_frame, text="로컬 md 올리기", command=self.on_upload
        )
        self.upload_btn.pack(side="left", padx=(8, 0))
        self._busy_widgets.append(self.upload_btn)

        self.status_var = tk.StringVar(value="준비됨")
        ttk.Label(self.root, textvariable=self.status_var).pack(
            anchor="w", padx=8
        )

        self.log_widget = scrolledtext.ScrolledText(self.root, height=16, width=80)
        self.log_widget.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log_widget.configure(state="disabled")

    # ---------- helpers ----------

    def log(self, text: str):
        if not text:
            return
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", text if text.endswith("\n") else text + "\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def set_busy(self, busy: bool, message: str = ""):
        state = "disabled" if busy else "normal"
        for widget in self._busy_widgets:
            widget.configure(state=state)
        if message:
            self.status_var.set(message)
        elif not busy:
            self.status_var.set("준비됨")

    @staticmethod
    def run_captured(func, *args, **kwargs):
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                result = func(*args, **kwargs)
            return True, result, buf.getvalue()
        except SystemExit as e:
            return False, None, buf.getvalue() + f"오류: {e}\n"
        except Exception as e:
            return False, None, buf.getvalue() + f"오류: {e}\n"

    # ---------- config ----------

    def on_browse_config(self):
        path = filedialog.askopenfilename(
            title="config.yaml 선택",
            filetypes=[("YAML", "*.yaml *.yml"), ("모든 파일", "*.*")],
        )
        if path:
            self.config_path_var.set(path)
            self.on_load_config()

    def on_load_config(self):
        path_str = self.config_path_var.get().strip()
        if not path_str:
            messagebox.showwarning("알림", "설정 파일 경로를 입력하세요.")
            return
        ok, cfg, out = self.run_captured(load_config, Path(path_str))
        self.log(out)
        if not ok:
            messagebox.showerror("오류", "설정 파일을 불러오지 못했습니다. 로그를 확인하세요.")
            return
        self.cfg = cfg
        self.vault_path_var.set(str(cfg["vault_path"]))
        self.repo_path_var.set(str(cfg["repo_path"]))
        self.refresh_branches()

    def on_browse_vault(self):
        path = filedialog.askdirectory(title="Vault 폴더 선택")
        if path:
            self.vault_path_var.set(path)

    def on_browse_repo(self):
        path = filedialog.askdirectory(title="Repo 폴더 선택 (git clone 해둔 폴더)")
        if path:
            self.repo_path_var.set(path)

    def _sync_cfg_from_fields(self):
        """Vault/Repo 입력 필드의 값을 현재 cfg에 즉시 반영 (저장 여부와 무관하게 동작에 적용)."""
        vault_path = self.vault_path_var.get().strip()
        repo_path = self.repo_path_var.get().strip()
        if not vault_path or not repo_path:
            return False
        if self.cfg is None:
            self.cfg = {
                "share_key": "share",
                "flatten": False,
                "target_subdir": "",
                "commit_message": "Update shared notes ({count} changed)",
                "auto_push": True,
                "branch": None,
            }
        self.cfg["vault_path"] = Path(vault_path).expanduser()
        self.cfg["repo_path"] = Path(repo_path).expanduser()
        return True

    def on_save_config(self):
        if not self._sync_cfg_from_fields():
            messagebox.showwarning("알림", "Vault 경로와 Repo 경로를 모두 입력하세요.")
            return

        branch = self.branch_var.get().strip()
        self.cfg["branch"] = branch or None

        path_str = self.config_path_var.get().strip()
        if not path_str:
            path_str = filedialog.asksaveasfilename(
                title="설정 저장 위치",
                defaultextension=".yaml",
                filetypes=[("YAML", "*.yaml *.yml")],
                initialfile="config.yaml",
            )
            if not path_str:
                return
            self.config_path_var.set(path_str)

        ok, _, out = self.run_captured(save_config, self.cfg, Path(path_str))
        self.log(out)
        if ok:
            messagebox.showinfo("저장 완료", f"{path_str}에 저장했습니다.")
            self.refresh_branches()
        else:
            messagebox.showerror("오류", "설정 저장에 실패했습니다. 로그를 확인하세요.")

    # ---------- branches ----------

    def refresh_branches(self):
        if not self.cfg:
            return
        self.set_busy(True, "브랜치 목록 불러오는 중...")

        def worker():
            ok, branches, out = self.run_captured(list_branches, self.cfg["repo_path"])
            cur = None
            if ok:
                ok2, cur, out2 = self.run_captured(
                    current_branch, self.cfg["repo_path"]
                )
                out += out2
            self._callback_queue.put(
                lambda: self._on_branches_loaded(ok, branches, out, cur)
            )

        threading.Thread(target=worker, daemon=True).start()

    def _on_branches_loaded(self, ok, branches, out, cur):
        self.set_busy(False)
        self.log(out)
        if not ok:
            messagebox.showerror("오류", "브랜치 목록을 가져오지 못했습니다. 로그를 확인하세요.")
            return
        self.branch_combo["values"] = branches
        target = self.cfg.get("branch") or cur
        if target:
            self.branch_var.set(target)

    # ---------- load branch locally ----------

    def on_load_branch(self):
        if not self._sync_cfg_from_fields():
            messagebox.showwarning("알림", "Vault 경로와 Repo 경로를 모두 입력하세요.")
            return
        branch = self.branch_var.get().strip()
        if not branch:
            messagebox.showwarning("알림", "브랜치를 선택하거나 입력하세요.")
            return

        self.set_busy(True, f"'{branch}' 브랜치 불러오는 중...")

        def worker():
            ok, _, out = self.run_captured(
                ensure_branch, self.cfg["repo_path"], branch
            )
            if ok:
                ok, _, out2 = self.run_captured(pull_latest, self.cfg["repo_path"])
                out += out2
            if ok:
                ok, _, out3 = self.run_captured(pull_into_vault, self.cfg)
                out += out3
            self._callback_queue.put(
                lambda: self._on_load_branch_done(ok, out, branch)
            )

        threading.Thread(target=worker, daemon=True).start()

    def _on_load_branch_done(self, ok, out, branch):
        self.set_busy(False)
        self.log(out)
        if ok:
            self.cfg["branch"] = branch
            self.status_var.set(f"'{branch}' 브랜치 준비 완료")
            self.refresh_branches()
        else:
            messagebox.showerror("오류", "브랜치를 불러오지 못했습니다. 로그를 확인하세요.")

    # ---------- upload ----------

    def on_upload(self):
        if not self._sync_cfg_from_fields():
            messagebox.showwarning("알림", "Vault 경로와 Repo 경로를 모두 입력하세요.")
            return
        branch = self.branch_var.get().strip()
        if not branch:
            messagebox.showwarning("알림", "브랜치를 선택하거나 입력하세요.")
            return
        self.cfg["branch"] = branch

        self.set_busy(True, "변경 사항 확인 중...")
        self.root.update_idletasks()

        ok, _, out = self.run_captured(ensure_branch, self.cfg["repo_path"], branch)
        self.log(out)
        if not ok:
            self.set_busy(False)
            messagebox.showerror("오류", "브랜치 전환에 실패했습니다. 로그를 확인하세요.")
            return

        ok, changed, out = self.run_captured(sync, self.cfg, True)
        self.log(out)
        if not ok:
            self.set_busy(False)
            messagebox.showerror("오류", "변경 사항 확인에 실패했습니다. 로그를 확인하세요.")
            return

        if not changed:
            self.set_busy(False)
            messagebox.showinfo("알림", "업로드할 변경 사항이 없습니다.")
            return

        proceed = messagebox.askyesno(
            "업로드 확인",
            f"'{branch}' 브랜치에 {changed}개 파일 변경 사항을 커밋하고 "
            f"push 할까요?\n\n자세한 목록은 로그 창을 확인하세요.",
        )
        if not proceed:
            self.set_busy(False)
            self.log("업로드를 취소했습니다.")
            return

        self.set_busy(True, "업로드 중...")

        def worker():
            ok, _, out1 = self.run_captured(sync, self.cfg, False)
            out_all = out1
            ok2 = False
            if ok:
                ok2, _, out2 = self.run_captured(
                    commit_and_push, self.cfg, changed, lambda status: True
                )
                out_all += out2
            self._callback_queue.put(
                lambda: self._on_upload_done(ok and ok2, out_all)
            )

        threading.Thread(target=worker, daemon=True).start()

    def _on_upload_done(self, ok, out):
        self.set_busy(False)
        self.log(out)
        if ok:
            messagebox.showinfo("완료", "업로드가 완료되었습니다.")
            self.refresh_branches()
        else:
            messagebox.showerror("오류", "업로드 중 오류가 발생했습니다. 로그를 확인하세요.")


def main():
    root = tk.Tk()
    ObsidianShareGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
