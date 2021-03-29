# Poetry rules for Bazel

`rules_python_poetry` makes Python packages from [Poetry](https://python-poetry.org/) lock files available
for use directly through [Bazel](https://bazel.build/).

Compared to traditional `requirements.txt`-based workflows, Poetry captures all transitive dependencies
for a consistent and deterministic environment. While there are other tools in this space such as `pip-compile`, they are typically more cumbersome to use.

Unlike [`rules_python`](https://github.com/bazelbuild/rules_python) and others, packages with
compatible wheels are managed completely within Bazel. `pip` is only used when packages must be built
from source, but even then Bazel downloads and caches the source distribution. This greatly speeds
up the dependency fetching process, especially for clean environments.

Note that `rules_python_poetry` only consumes the `poetry.lock` file. The Poetry-managed environments
are not used and can be avoided by only using `poetry lock` or passing `--lock` to most commands,
e.g., `poetry add foo==1.2.3 --lock`.

## Getting Started

Add the following to your `WORKSPACE` file:

```py
load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

http_archive(
    name = "rules_python_poetry",
    strip_prefix = "rules_python_poetry-master",
    urls = [
        "https://github.com/kornholi/rules_python_poetry/archive/refs/heads/master.zip",
    ],
)

load("@rules_python_poetry//:defs.bzl", "poetry_import")

poetry_import(
    name = "pypi_deps",
    lock_file = "//:poetry.lock",
)

load("@pypi_deps//:packages.bzl", "python_deps")

python_deps()
```

Then use the `pypi` macro in your `BUILD` files:

```py
load("@pypi_deps//:packages.bzl", "pypi")

py_binary(
    name = "foo",
    srcs = ["foo.py"],
    deps = [
        pypi("requests"),
    ]
)
```
For easier interoperability with `rules_python`, you can also use the identical `requirement` macro
from `requirements.bzl`:

```py
load("@pypi_deps//:requirements.bzl", "requirement")
```

### Overriding packages

In some cases you might want to replace a transitive dependency with a version within your
workspace. `override_pkg` in `poetry_import` lets you specify a Bazel label to be used instead of
the version released on PyPI.

For example, we can replace `protobuf` with a Bazel-built version instead:

```py
poetry_import(
    name = "pypi_deps",
    lock_file = "//:poetry.lock",
    override_pkg = {
        "protobuf": "@com_google_protobuf//:protobuf_python"
    }
)
```

## Future Work
- Overriding python interpreter
- Improve reproducibility of pip-installed packages
- Support for private repositories
- Support for git-hosted packages
- Overriding build files (e.g. to expose numpy headers)
- Expose poetry as a Bazel target
- Wheel selection through Bazel platforms & constraints
- Skip unpacking wheels and rely on zipimport at runtime (should greatly reduce runfiles' symlink churn)
- Expose `console_scripts`/`entry_points` as Bazel targets

## Known Issues

### Missing setuptools/pip/wheel packages

Poetry currently considers four packages as special: setuptools, distribute, pip, and wheel. They are
managed directly by Poetry and thus not included in the `Poetry.lock`. This limitation will go away
once [PR 2826](https://github.com/python-poetry/poetry/pull/2826) gets merged in and released.

### ModuleNotFoundError: No module named 'pkg_resources'

See above section about missing setuptools.

### ModuleNotFoundError with namespaced packages (e.g. `google`, `azure`)
Bazel's legacy implicit `__init__.py` creation breaks [PEP 420](https://www.python.org/dev/peps/pep-0420/) namespaces. It can be disabled with the [`legacy_create_init`](https://docs.bazel.build/versions/master/be/python.html#py_binary.legacy_create_init) attribute in `py_binary`/`py_image` targets, or globally with [`--incompatible_default_to_explicit_init_py`](https://github.com/bazelbuild/bazel/issues/10076).
