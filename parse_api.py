import re
import ast

def parse_py(file_path):
    with open(file_path, "r") as f:
        source = f.read()

    tree = ast.parse(source)
    methods = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Api":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and not child.name.startswith("_"):
                    args = []
                    for a in child.args.args[1:]: # skip self
                        args.append(a.arg)
                    methods[child.name] = args
    return methods

py_methods = parse_py("core/api.py")

def parse_ts(file_path):
    with open(file_path, "r") as f:
        source = f.read()

    methods = {}

    # regex for `async methodName(` over multiple lines or inline
    # just find `async methodName` in the file.
    matches = re.finditer(r'async ([a-zA-Z0-9_]+)\s*\(', source)
    for match in matches:
        name = match.group(1)
        if name in ["invoke", "invokeLibraryChange"]:
            continue

        # find the closing parenthesis for args
        start = match.end()
        paren_count = 1
        i = start
        while i < len(source) and paren_count > 0:
            if source[i] == '(':
                paren_count += 1
            elif source[i] == ')':
                paren_count -= 1
            i += 1

        args_str = source[start:i-1]
        args = []
        if args_str.strip():
            # naive split by comma, ignoring nested brackets - good enough for these signatures
            arg_list = re.split(r',\s*(?![^{}]*})', args_str)
            for a in arg_list:
                a = a.split(":")[0].strip().split("=")[0].strip()
                if a:
                    args.append(a)
        methods[name] = args
    return methods

ts_methods = parse_ts("ui/src/services/api.ts")

camel_to_snake = lambda name: re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()
snake_to_camel = lambda name: re.sub(r'_([a-z])', lambda m: m.group(1).upper(), name)

mismatches = []

for py_name, py_args in py_methods.items():
    ts_name = snake_to_camel(py_name)
    if ts_name not in ts_methods and py_name not in ts_methods:
        if py_name not in ["complete_dropbox_callback", "complete_gdrive_callback", "set_window", "read"]:
            print(f"Missing in TS: {py_name} py_args={py_args}")
    else:
        found_name = ts_name if ts_name in ts_methods else py_name
        ts_args = ts_methods[found_name]

        if len(py_args) != len(ts_args):
            print(f"Arg mismatch for {py_name}: py={py_args}, ts={ts_args}")
