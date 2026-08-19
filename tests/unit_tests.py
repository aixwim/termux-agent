"""Uji unit: tool handler, konversi pesan Anthropic, registri."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from termux_agent.tools import files, search, shell  # noqa: F401  # register tools
from termux_agent.tools.base import ToolContext, run_tool, tool_specs

PASS = 0


def check(name: str, cond: bool):
    global PASS
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}")


tmp = Path(tempfile.mkdtemp())
(ctx_file, ctx_grep, ctx_shell) = (
    ToolContext(working_dir=tmp, confirm_commands=False),
    ToolContext(working_dir=tmp, confirm_commands=False),
    ToolContext(working_dir=tmp, confirm_commands=True, confirm=lambda c: True),
)

# --- registri ---
specs = {s.name for s in tool_specs()}
check("8 tools terdaftar", len(specs) == 8)
check("tools lengkap", {"read_file", "write_file", "edit_file", "list_dir", "grep_file", "glob_find", "run_command", "web_fetch"} <= specs)

# --- write/read/edit ---
f = tmp / "hello.py"
check("write_file", "OK" in run_tool("write_file", {"path": "hello.py", "content": "a=1\nb=2\n"}, ctx_file))
check("read_file", "a=1" in run_tool("read_file", {"path": "hello.py"}, ctx_file))
check("edit_file", "OK" in run_tool("edit_file", {"path": "hello.py", "old_string": "b=2", "new_string": "c=3"}, ctx_file))
dup = tmp / "dup.txt"
dup.write_text("x\nx\n")
check("edit_file tidak unik", "lebih unik" in run_tool("edit_file", {"path": "dup.txt", "old_string": "x", "new_string": "y"}, ctx_file))
r = run_tool("read_file", {"path": "hello.py"}, ctx_file)
check("isi terganti", "c=3" in r and "b=2" not in r)

# --- path escape ---
outside = Path("/tmp/escape_test.txt")
outside.write_text("secret")
check("akses di luar ditolak", "ditolak" in run_tool("read_file", {"path": str(outside)}, ctx_file))

# --- grep / glob ---
(tmp / "sub").mkdir()
(tmp / "sub" / "app.py").write_text("def main():\n    print('x')\n")
check("grep_file", "app.py" in run_tool("grep_file", {"pattern": "def main"}, ctx_grep))
check("glob_find", "sub/app.py" in run_tool("glob_find", {"pattern": "**/*.py"}, ctx_grep))
check("list_dir", "app.py" in run_tool("list_dir", {"path": "sub"}, ctx_grep))

# --- shell ---
check("run_command aman", "exit 0" in run_tool("run_command", {"command": "echo halo"}, ctx_shell))
ctx_confirm = ToolContext(working_dir=tmp, confirm_commands=True, confirm=lambda c: True)
check("run_command non-whitelist dikonfirmasi", "exit 0" in run_tool("run_command", {"command": "touch x"}, ctx_confirm))
ctx_refuse = ToolContext(working_dir=tmp, confirm_commands=True, confirm=lambda c: False)
check("perintah ditolak", "Dibatalkan" in run_tool("run_command", {"command": "touch y"}, ctx_refuse))
check("perintah gagal", "exit 1" in run_tool("run_command", {"command": "false"}, ctx_shell))
check("shell di working_dir", "hello.py" in run_tool("run_command", {"command": "ls"}, ctx_shell))
check("timeout", "batas waktu" in run_tool("run_command", {"command": "sleep 5"}, ToolContext(working_dir=tmp, confirm_commands=False, command_timeout=1)))

# --- konversi Anthropic ---
from termux_agent.providers.anthropic import AnthropicProvider

sysp, msgs = AnthropicProvider._to_anthropic(
    [
        {"role": "system", "content": "sistem"},
        {"role": "user", "content": "hai"},
        {"role": "assistant", "content": "ok", "tool_calls": [{"id": "tc1", "name": "ls", "arguments": '{"a":1}'}]},
        {"role": "tool", "tool_call_id": "tc1", "content": "hasil"},
    ]
)
check("anthropic system field", sysp == "sistem")
check("anthropic assistant blocks", msgs[1]["content"][1] == {"type": "tool_use", "id": "tc1", "name": "ls", "input": {"a": 1}})
check("anthropic tool_result", msgs[2]["content"][0]["type"] == "tool_result")

print(f"\n{PASS} check lulus")
sys.exit(0 if PASS else 1)