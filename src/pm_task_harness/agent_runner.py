from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AgentResult:
    output: str
    command: list[str]
    returncode: int


class AgentRunner:
    def __init__(self, provider: str, workdir: Path, model: Optional[str] = None, timeout_seconds: int = 300):
        if provider not in {"codex", "kimi"}:
            raise ValueError("provider must be codex or kimi")
        self.provider = provider
        self.workdir = workdir
        self.model = model
        self.timeout_seconds = timeout_seconds

    def run(self, prompt: str, output_file: Path, resume: bool = False) -> AgentResult:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if self.provider == "codex":
            # Codex can write directly to output_file via -o. On resume the CLI
            # does not accept the same -C/-s flags, so command construction is
            # split rather than trying to share one argument template.
            cmd = self._codex_command(output_file, resume)
            try:
                proc = subprocess.run(
                    cmd,
                    input=prompt,
                    text=True,
                    cwd=self.workdir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
                output += f"\nerror: agent timed out after {self.timeout_seconds}s"
                output_file.write_text(output, encoding="utf-8")
                return AgentResult(output=output, command=cmd, returncode=124)
            output = output_file.read_text(encoding="utf-8") if output_file.exists() else proc.stdout
            return AgentResult(output=output, command=cmd, returncode=proc.returncode)

        # Kimi prompt mode writes to stdout. The harness owns the artifact file
        # so validator behavior is identical across providers.
        cmd = self._kimi_command(prompt, resume)
        try:
            proc = subprocess.run(
                cmd,
                text=True,
                cwd=self.workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            output += f"\nerror: agent timed out after {self.timeout_seconds}s"
            output_file.write_text(output, encoding="utf-8")
            return AgentResult(output=output, command=cmd, returncode=124)
        output_file.write_text(proc.stdout, encoding="utf-8")
        return AgentResult(output=proc.stdout, command=cmd, returncode=proc.returncode)

    def _codex_command(self, output_file: Path, resume: bool) -> list[str]:
        if resume:
            base = [
                "codex",
                "exec",
                "resume",
                "--last",
                "--skip-git-repo-check",
                "-o",
                str(output_file),
            ]
            if self.model:
                base += ["-m", self.model]
            base.append("-")
            return base

        base = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "-C",
            str(self.workdir),
            "-s",
            "read-only",
            "--color",
            "never",
            "-o",
            str(output_file),
        ]
        if self.model:
            base += ["-m", self.model]
        base.append("-")
        return base

    def _kimi_command(self, prompt: str, resume: bool) -> list[str]:
        # Kimi CLI rejects prompt mode combined with yolo/auto flags; keep this
        # command minimal unless a future test proves a new flag is compatible.
        cmd = ["kimi", "-p", prompt]
        if resume:
            cmd.insert(1, "-C")
        if self.model:
            cmd[1:1] = ["-m", self.model]
        return cmd
