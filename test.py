import os
import glob
import subprocess

print("=== Searching for mmdc ===\n")

# Check npm prefix
try:
    result = subprocess.run(["npm", "config", "get", "prefix"], capture_output=True, text=True, shell=True)
    npm_prefix = result.stdout.strip()
    print(f"npm prefix: {npm_prefix}")
    
    # Search in npm prefix
    search_paths = [
        os.path.join(npm_prefix, "mmdc.cmd"),
        os.path.join(npm_prefix, "node_modules", ".bin", "mmdc.cmd"),
    ]
    
    for path in search_paths:
        print(f"  Checking: {path} - {'EXISTS' if os.path.exists(path) else 'NOT FOUND'}")
        
except Exception as e:
    print(f"npm check failed: {e}")

# Search common locations
print("\n=== Searching common paths ===")
patterns = [
    os.path.expandvars(r"%APPDATA%\npm\mmdc.cmd"),
    os.path.expandvars(r"%LOCALAPPDATA%\npm\mmdc.cmd"),
    r"C:\Users\*\AppData\Roaming\npm\mmdc.cmd",
    r"C:\Program Files\nodejs\mmdc.cmd",
]

for pattern in patterns:
    if "*" in pattern:
        matches = glob.glob(pattern)
        for match in matches:
            print(f"Found: {match}")
    else:
        exists = os.path.exists(pattern)
        print(f"{'[X]' if exists else '[ ]'} {pattern}")

# Check PATH
print("\n=== Current PATH ===")
for p in os.environ.get("PATH", "").split(os.pathsep)[:5]:
    if "npm" in p.lower() or "node" in p.lower():
        print(f"  -> {p} [RELEVANT]")
    else:
        print(f"     {p}")