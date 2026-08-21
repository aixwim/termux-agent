"""Tool registry and shared execution context for all tools."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

TOOL_REGISTRY: dict[str, dict[str, Any]] = {}

ConfirmFn = Callable[[str], bool]


@dataclass
class ToolContext:
    working_dir: Path
    max_output_chars: int = 60000
    command_timeout: int = 60
    confirm_commands: bool = True
    confirm: ConfirmFn | None = None
    _allowed_dirs: list[Path] = field(default_factory=list)
    undo_stack: list[dict] = field(default_factory=list)
    whitelisted_commands: list[str] = field(default_factory=list)
    _roots_signature: tuple[str, ...] = field(default=(), init=False, repr=False)
    _resolved_roots: tuple[Path, ...] = field(default=(), init=False, repr=False)

    def resolve(self, path: str) -> Path:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.working_dir / p
        return p.resolve()

    def is_allowed(self, path: Path) -> bool:
        resolved = path.resolve()
        # Resolving roots is comparatively expensive on Android filesystems.
        # Cache roots, but always resolve the target so a symlink swap cannot
        # bypass the boundary check. The signature invalidates automatically
        # when callers replace or mutate the public allow-list.
        signature = (str(self.working_dir), *(str(d) for d in self._allowed_dirs))
        if signature != self._roots_signature:
            self._resolved_roots = tuple(Path(root).resolve() for root in signature)
            self._roots_signature = signature
        return any(resolved == root or root in resolved.parents for root in self._resolved_roots)

    def require_allowed(self, path: Path) -> Path:
        if not self.is_allowed(path):
            raise PermissionError(f"Access denied: outside working directory ({self.working_dir})")
        return path

    def _snapshot(self, path: Path) -> None:
        """Record the current state of a file before it is modified."""
        resolved = path.resolve()
        if resolved.is_file():
            content = resolved.read_text(encoding="utf-8", errors="replace")
        else:
            content = None
        self.undo_stack.append({"path": resolved, "existed": content is not None, "content": content})

    def undo(self) -> str:
        """Restore the most recently modified file to its previous state."""
        if not self.undo_stack:
            return "Nothing to undo."
        entry = self.undo_stack[-1]
        path: Path = entry["path"]
        try:
            if entry["existed"]:
                from termux_agent.storage import atomic_write_text

                atomic_write_text(path, entry["content"])
                self.undo_stack.pop()
                return f"Undid: restored original content of {path}"
            if path.exists():
                path.unlink()
            self.undo_stack.pop()
            return f"Undid: removed {path} (it did not exist before)"
        except OSError as e:
            return f"Error: cannot undo {path}: {e}"


def tool(name: str, description: str, parameters: dict[str, Any]):
    def deco(fn: Callable[[dict[str, Any], ToolContext], str]) -> Callable:
        TOOL_REGISTRY[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "fn": fn,
        }
        return fn

    return deco


def run_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> str:
    entry = TOOL_REGISTRY.get(name)
    if not entry:
        return f"Error: unknown tool '{name}'."
    try:
        result = entry["fn"](args, ctx)
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001
        return f"Error ({type(e).__name__}): {e}"
    if len(result) > ctx.max_output_chars:
        result = result[: ctx.max_output_chars] + "\n... [output truncated]"
    return result


def tool_specs() -> list[dict]:
    from termux_agent.providers.base import ToolSpec

    return [
        ToolSpec(
            name=e["name"],
            description=e["description"],
            parameters=e["parameters"],
        )
        for e in sorted(TOOL_REGISTRY.values(), key=lambda x: x["name"])
    ]
