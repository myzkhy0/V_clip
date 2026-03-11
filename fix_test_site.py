import pathlib

p = pathlib.Path(r"f:\antigravity\V_clip\test_site.py")
text = p.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)
print(f"Total lines: {len(lines)}")

# Find the FIRST triple-quote after line 1670 (0-indexed)
first_close = None
for i in range(1670, len(lines)):
    if lines[i].strip() == '"""':
        first_close = i
        break

# Find "class TestSiteHandler"
handler_start = None
for i in range(len(lines)):
    if "class TestSiteHandler" in lines[i]:
        handler_start = i
        break

print(f"First close triple-quote at line {first_close + 1}: {repr(lines[first_close].rstrip())}")
print(f"TestSiteHandler at line {handler_start + 1}: {repr(lines[handler_start].rstrip())}")

# Keep: lines 0..first_close (inclusive) + blank + handler_start..end
keep = lines[:first_close + 1] + ["\n", "\n"] + lines[handler_start:]
print(f"Keeping {len(keep)} lines (removed {len(lines) - len(keep)} lines)")

p.write_text("".join(keep), encoding="utf-8")
print("Done!")
