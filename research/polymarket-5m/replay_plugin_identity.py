"""Source snapshots for small, offline, pure-feature research plugins.

Static local Python imports (including relative imports and package __init__)
form a content-addressed closure. Every module executes the exact bytes hashed;
we neither consume nor write .pyc and do not reuse local sys.modules entries.
Standard-library code is represented by the interpreter identity, not rehashed.

This is reproducibility plumbing, not an untrusted-code sandbox. Dynamic imports,
code generation, namespace packages and third-party binary extensions are outside
this contract and fail closed. Optional REPLAY_DEPENDENCIES is a literal list of
local Python module names; it cannot authorize dynamic loading or missing files.
"""
from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import platform
import sys
import sysconfig
import types

_FORBIDDEN_IMPORTS = {'importlib', 'pkgutil', 'runpy', 'builtins', 'sys'}
_FORBIDDEN_NAMES = {'__import__', '__builtins__', 'eval', 'exec', 'compile'}
_STDLIB = Path(sysconfig.get_path('stdlib')).resolve()
_SITE_PATHS = {Path(sysconfig.get_path(k)).resolve() for k in ('purelib', 'platlib')}
_REAL_IMPORT = builtins.__import__


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def python_identity():
    return {'implementation': platform.python_implementation(), 'version': sys.version,
            'cache_tag': sys.implementation.cache_tag, 'optimize': sys.flags.optimize}


@dataclass(frozen=True)
class Source:
    name: str
    path: Path
    data: bytes
    package: bool

    @property
    def sha256(self):
        return _digest(self.data)


class PluginSnapshot:
    """Build once before registration; use identity in the replay cache key."""
    def __init__(self, name):
        if not isinstance(name, str) or not name or any(not p.isidentifier() for p in name.split('.')):
            raise ValueError('plugin must be a dotted Python module name')
        self.root = name
        self.sources = {}
        self._resolved = {}
        self._modules = {}
        self._stdlib_imports = set()
        self._collect(name)
        self.identity = {'schema': 'replay-plugin-source-closure-v1', 'root_module': name,
                         'module_sha256': {n: s.sha256 for n, s in sorted(self.sources.items())},
                         'stdlib_imports': sorted(self._stdlib_imports),
                         'python': python_identity(),
                         'loader_sha256': _digest(Path(__file__).read_bytes())}
        self.identity_sha256 = _digest(json.dumps(self.identity, sort_keys=True,
                                                separators=(',', ':')).encode())
        self.source_paths = {n: str(s.path) for n, s in sorted(self.sources.items())}

    def _resolve(self, name, optional=False):
        if name in self._resolved:
            return self._resolved[name]
        parent, _, _ = name.rpartition('.')
        if parent:
            p = self._resolve(parent)
            if p == 'stdlib':
                return 'stdlib'
            if not p.package:
                if optional:
                    return None
                raise ValueError(f'{parent} is not a package for dependency {name}')
            spec = importlib.machinery.PathFinder.find_spec(name, [str(p.path.parent)])
        else:
            spec = (importlib.machinery.BuiltinImporter.find_spec(name)
                    or importlib.machinery.FrozenImporter.find_spec(name)
                    or importlib.machinery.PathFinder.find_spec(name))
        if spec is None:
            if optional:
                return None
            raise ValueError(f'missing declared/imported dependency: {name}')
        origin = spec.origin
        if origin in ('built-in', 'frozen'):
            result = 'stdlib'
        else:
            if origin is None:
                raise ValueError(f'namespace/non-file dependency unsupported: {name}')
            path = Path(origin).resolve()
            in_stdlib = (path.is_relative_to(_STDLIB)
                         and not any(path.is_relative_to(p) for p in _SITE_PATHS))
            if name.split('.')[0] in sys.stdlib_module_names and in_stdlib:
                result = 'stdlib'
            elif path.suffix == '.py' and path.is_file():
                result = Source(name, path, path.read_bytes(), spec.submodule_search_locations is not None)
            else:
                raise ValueError(f'only Python source dependencies are supported: {name} ({origin})')
        self._resolved[name] = result
        return result

    def _collect(self, name):
        if name.split('.')[0] in _FORBIDDEN_IMPORTS:
            raise ValueError(f'dynamic/module-state import unsupported in research plugin: {name}')
        source = self._resolve(name)
        if source == 'stdlib':
            self._stdlib_imports.add(name)
            return
        if name in self.sources:
            return
        parent = name.rpartition('.')[0]
        if parent:
            self._collect(parent)
        self.sources[name] = source  # Reserve before recursion for import cycles.
        try:
            tree = ast.parse(source.data, filename=str(source.path))
        except (SyntaxError, UnicodeError) as exc:
            raise ValueError(f'invalid plugin source {name}: {exc}') from exc
        package = name if source.package else parent
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
                raise ValueError(f'dynamic Python loading/code generation unsupported: {name}:{node.lineno} {node.id}')
            if isinstance(node, ast.Import):
                for item in node.names:
                    self._collect(item.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    try:
                        target = importlib.util.resolve_name('.' * node.level + (node.module or ''), package)
                    except (ImportError, ValueError) as exc:
                        raise ValueError(f'invalid relative dependency in {name}') from exc
                else:
                    target = node.module
                self._collect(target)
                # from package import helper may name a submodule or an attribute.
                if self._resolve(target) != 'stdlib':
                    for item in node.names:
                        if item.name != '*' and self._resolve(target + '.' + item.name, optional=True) is not None:
                            self._collect(target + '.' + item.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id == 'REPLAY_DEPENDENCIES' for t in targets):
                    try:
                        declared = ast.literal_eval(node.value)
                    except (ValueError, TypeError) as exc:
                        raise ValueError(f'{name} REPLAY_DEPENDENCIES must be a literal list') from exc
                    if (not isinstance(declared, (list, tuple)) or
                            any(not isinstance(x, str) or not x or
                                any(not p.isidentifier() for p in x.split('.')) for x in declared)):
                        raise ValueError(f'{name} REPLAY_DEPENDENCIES must contain module names')
                    for dependency in declared:
                        self._collect(dependency)

    def verify(self):
        """Reject edits/deletions after registration instead of labeling old code new."""
        if self.identity['python'] != python_identity():
            raise ValueError('plugin interpreter identity changed after registration')
        if self.identity['loader_sha256'] != _digest(Path(__file__).read_bytes()):
            raise ValueError('plugin dependency loader changed after registration')
        for name, source in self.sources.items():
            try:
                current = source.path.read_bytes()
            except OSError as exc:
                raise ValueError(f'plugin dependency disappeared after registration: {name}') from exc
            if _digest(current) != source.sha256:
                raise ValueError(f'plugin dependency changed after registration: {name}')

    def _import(self, name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            try:
                name = importlib.util.resolve_name('.' * level + name, (globals or {}).get('__package__', ''))
            except (ImportError, ValueError) as exc:
                raise ImportError(f'invalid relative import {name}') from exc
        if name.split('.')[0] in _FORBIDDEN_IMPORTS:
            raise ValueError(f'dynamic/module-state import unsupported: {name}')
        if name not in self.sources:
            if self._resolve(name) == 'stdlib':
                return _REAL_IMPORT(name, globals, locals, fromlist, 0)
            raise ValueError(f'unregistered dependency import: {name}')
        module = self._load(name)
        if fromlist:
            for item in fromlist:
                if item != '*' and name + '.' + item in self.sources:
                    self._load(name + '.' + item)
            return module
        return self._load(name.split('.')[0])

    def _load(self, name):
        if name in self._modules:
            return self._modules[name]
        source = self.sources[name]
        parent_name, _, leaf = name.rpartition('.')
        parent = self._load(parent_name) if parent_name else None
        # A parent's __init__ may have loaded this child recursively.
        if name in self._modules:
            return self._modules[name]
        module = types.ModuleType(name)
        module.__file__ = str(source.path)
        module.__package__ = name if source.package else parent_name
        module.__spec__ = importlib.util.spec_from_file_location(name, source.path,
                            submodule_search_locations=[str(source.path.parent)] if source.package else None)
        if source.package:
            module.__path__ = [str(source.path.parent)]
        module.__dict__['__builtins__'] = {**vars(builtins), '__import__': self._import}
        self._modules[name] = module
        if parent is not None:
            setattr(parent, leaf, module)
        try:
            # Dataclasses and similar stdlib decorators inspect sys.modules.
            # Install only during compilation, then restore foreign modules.
            saved = {n: sys.modules.get(n) for n in self._modules}
            sys.modules.update(self._modules)
            try:
                exec(compile(source.data, str(source.path), 'exec', dont_inherit=True), module.__dict__)
            finally:
                for n, prior in saved.items():
                    if prior is None:
                        sys.modules.pop(n, None)
                    else:
                        sys.modules[n] = prior
        except BaseException:
            self._modules.pop(name, None)
            if parent is not None and getattr(parent, leaf, None) is module:
                delattr(parent, leaf)
            raise
        return module

    def load(self):
        self.verify()
        module = self._load(self.root)
        self.verify()
        return module


def load_plugin(name):
    """Return (module, snapshot); retain snapshot through execution and verify()."""
    snapshot = PluginSnapshot(name)
    return snapshot.load(), snapshot
