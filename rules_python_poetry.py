import sys, os

if sys.version_info < (3, 6):
    sys.exit("rules_python_poetry requires Python 3.6+")

VENDORED_DEPS = [
    "third_party/pyparsing-2.4.7-py2.py3-none-any.whl",
    "third_party/packaging-20.9-py2.py3-none-any.whl",
    "third_party/tomlkit-0.7.0-py2.py3-none-any.whl",
    "third_party/urllib3-1.26.4-py2.py3-none-any.whl",
]
for dep in VENDORED_DEPS:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), dep))

import argparse
import atexit
import json
from packaging.markers import Marker, UndefinedEnvironmentName, Variable
import packaging.tags
import tomlkit
import urllib3

# TODO:
# - Better verbose rule attr
# - maybe-ify workspace rules?

HTTP_ARCHIVE_TEMPLATE = """
    http_archive(
        name = "{name}",
        build_file_content = {build_file_content},
        sha256 = "{sha256}",
        type = "zip",
        urls = [
            "{url}",
        ],
    )

    """

SDIST_BUILD_TEMPLATE = """
    pip_install_sdist(
        name = "{name}",
        build_file_content = {build_file_content},
        sha256 = "{sha256}",
        url = "{url}",
    )
"""


class PyPILinkResolver:
    """
    We need to fetch the packages from somewhere, but poetry lock files only hold their filenames
    and SHA256 hashes.

    Canonical PyPI URLs include a BLAKE2 hash, which we don't have. There's a legacy endpoint
    without hashes which we could use [1], but it doesn't always work the way you'd expect--for a
    small number of files, the python version on PyPI does not match the python version in the
    filename. For example, `idna-2.8-py2.py3-none-any.whl` has a python version of `3.7` instead of
    the expected `py2.py3`.

    Given that the endpoint simply calls the public JSON API to redirect to the canonical url [2],
    we're going to do the same here.

    [1] https://files.pythonhosted.org/packages/python_version/p/package/package-ver-pyver-abi-plat.whl
    [2] https://github.com/pypa/conveyor/blob/master/conveyor/views.py#L74-L75
    """

    def __init__(self):
        self._pool = urllib3.PoolManager()
        self._cache = {}
        self._cache_modified = False
        self._load_cache()
        atexit.register(self._save_cache)

    def _cache_path(self):
        if sys.platform == "darwin":
            base_path = os.path.expanduser("~/Library/Caches")
        else:
            base_path = os.getenv("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))

        return os.path.join(base_path, "rules_python_poetry", "pypi_cache.json")

    def _load_cache(self):
        try:
            with open(self._cache_path(), "r") as f:
                saved_cache = json.load(f)

            if isinstance(saved_cache, dict) and saved_cache.get("version") == 1:
                self._cache = saved_cache["data"]
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            pass

    def _save_cache(self):
        if not self._cache_modified:
            return

        cache_path = self._cache_path()
        os.makedirs(os.path.dirname(cache_path), mode=0o755, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump({"version": 1, "data": self._cache}, f, indent=4)

    def _get_metadata(self, pkg: str):
        response = self._pool.request("GET", f"https://pypi.org/pypi/{pkg}/json")

        if response.status == 404:
            raise Exception(f"Package {pkg} does not exist on PyPI")

        if response.status != 200:
            raise Exception(
                f"Failed to get PyPI metadata for {pkg} with http status {response.status}"
            )

        return json.loads(response.data.decode())

    def get_url(self, pkg: str, version: str, sha256: str) -> str:
        cached_url = self._cache.get(sha256)
        if cached_url:
            return cached_url

        pkg_metadata = self._get_metadata(pkg)

        for pkg_version, files in pkg_metadata["releases"].items():
            if pkg_version != version:
                continue

            for file in files:
                if file["digests"]["sha256"] != sha256:
                    continue

                print(f"Found {file['filename']} at {file['url']}")

                self._cache[file["digests"]["sha256"]] = file["url"]
                self._cache_modified = True
                return file["url"]

        return None


def remove_extra_marker(markers):
    rewritten_markers = []

    for marker in markers:
        assert isinstance(marker, (list, tuple, str))

        if isinstance(marker, list):
            rewritten_markers.append(remove_extra_marker(marker))
        elif isinstance(marker, tuple):
            lhs, op, rhs = marker

            if isinstance(lhs, Variable):
                variable = lhs.value
            else:
                variable = rhs.value

            if variable == "extra":
                continue

            rewritten_markers.append(marker)
        else:
            rewritten_markers.append(marker)

    return rewritten_markers


def evaluated_deps(pkg):
    "Returns dependencies that apply to the current environment"

    deps = []
    for k, dep in pkg.get("dependencies", {}).items():
        if "markers" in dep:
            marker = Marker(dep["markers"])

            # Remove any "extra" clauses from the marker before we evaluate it. If a package is
            # in this list, the extra has already been matched.
            marker._markers = remove_extra_marker(marker._markers)

            try:
                if not marker.evaluate():
                    continue
            except UndefinedEnvironmentName as e:
                print(f"Failed to evaluate marker for {pkg['name']}: {e}")
                raise

        deps.append(k)
    return deps


def extract_wheel_filename_tags(filename):
    "Extracts tags from a wheel filename"

    assert filename.endswith(".whl")
    filename = filename[:-4]
    _, interpreters, abis, platforms = filename.rsplit("-", 3)

    for interpreter in interpreters.split("."):
        for abi in abis.split("."):
            for platform in platforms.split("."):
                yield packaging.tags.Tag(interpreter, abi, platform)


def best_compatible_file(files, compatible_tags):
    """
    Given a list files for a package, pick the most applicable one. This assumes that the
    best-matching tag comes first in compatible_tags.
    """
    source_dist = None
    best_wheel = None
    best_wheel_tag_idx = None

    for f in files:
        filename = f["file"]

        if filename.endswith(".whl"):
            for tag in extract_wheel_filename_tags(filename):
                try:
                    compatible_tag_index = compatible_tags.index(tag)

                    if not best_wheel or compatible_tag_index < best_wheel_tag_idx:
                        best_wheel = f
                        best_wheel_tag_idx = compatible_tag_index
                except ValueError:
                    # Wheel is not compatible
                    pass
        # FIXME: Are there any other extensions we need to care about?
        elif filename.endswith(".tar.gz") or filename.endswith(".zip"):
            source_dist = f

    if best_wheel:
        return {"type": "wheel", **best_wheel}

    if source_dist:
        return {"type": "source", **source_dist}

    return None


def format_bazel_dist(bazel_workspace_name, dist, deps):
    "Formats bazel workspace rule for a wheel or sdist package"

    templated_build_contents = (
        """render_package_build(name="{workspace_name}", deps=[{deps}])""".format(
            workspace_name=bazel_workspace_name,
            deps=",".join(f'pypi("{dep}")' for dep in deps),
        )
    )

    file_sha256 = dist["hash"][len("sha256:") :]
    url = dist["url"]

    if dist["type"] == "wheel":
        return HTTP_ARCHIVE_TEMPLATE.format(
            name=bazel_workspace_name,
            build_file_content=templated_build_contents,
            sha256=file_sha256,
            url=url,
        )

    if dist["type"] == "source":
        # Build time deps? Not much we can do here:
        # - https://github.com/python-poetry/poetry/issues/1307
        # - https://github.com/python-poetry/poetry/issues/2778
        return SDIST_BUILD_TEMPLATE.format(
            name=bazel_workspace_name,
            build_file_content=templated_build_contents,
            sha256=file_sha256,
            url=url,
        )


def get_dist_url(dist, pkg, pypi_resolver: PyPILinkResolver):
    assert dist["hash"].startswith("sha256:"), "file hash is not sha256"
    file_sha256 = dist["hash"][len("sha256:") :]

    if dist["type"] == "wheel":
        url = pypi_resolver.get_url(pkg["name"], pkg["version"], file_sha256)
        if not url:
            raise Exception(f"Could not resolve PyPI url for {dist['file']}")

        return url

    if dist["type"] == "source":
        url = f"https://files.pythonhosted.org/packages/source/{pkg['name'][0]}/{pkg['name']}/{dist['file']}"
        return url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lock_file", default="poetry.lock")
    parser.add_argument("--root-workspace", required=True)
    parser.add_argument("--extra-dependency", action="append")
    parser.add_argument(
        "--override-pkg", action="append", type=lambda kv: kv.split("=", 1)
    )
    args = parser.parse_args()

    lock_file = tomlkit.parse(open(args.lock_file, "r").read())

    output = open("packages.bzl", "w")
    output.write(
        """# Generated by rules_python_poetry
# DO NOT EDIT!

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")
load("@rules_python_poetry//:defs.bzl", "pip_install_sdist")

BUILD_TEMPLATE = '''
package(default_visibility = ["//visibility:public"])

load("@rules_python//python:defs.bzl", "py_library")

py_library(
    name = "{workspace_name}",
    srcs = glob(["**/*.py"], allow_empty = True),
    data = glob(["**/*"], exclude=["**/*.py", "**/* *", "BUILD", "WORKSPACE"]),
    # This makes this directory a top-level in the python import search path
    # for anything that depends on this.
    imports = ["."],
    deps = [{deps}],
)
'''

def render_package_build(name, deps):
    return BUILD_TEMPLATE.format(
        workspace_name=name,
        deps=",".join(["\\"{}\\"".format(dep) for dep in deps])
    )

def pypi(name):
    name_key = name.lower()
    if name_key not in packages:
        fail("Could not find poetry dependency: '%s'" % name)
    return packages[name_key]

"""
    )

    compatible_tags = list(packaging.tags.sys_tags())
    pypi_resolver = PyPILinkResolver()
    packages = {}

    output.write("def python_deps():\n")
    for pkg in lock_file["package"]:
        if pkg.get("source"):
            # For git via pip: {url}@{resolved_resource_reference}#egg={package_name}
            print(f'FIXME: Need to build {pkg["name"]} from source repo!')
            continue

        files = lock_file["metadata"]["files"][pkg["name"]]

        dist = best_compatible_file(files, compatible_tags)
        if not dist:
            # Some packages don't have a source distribution available and none of the wheels might
            # be compatible, e.g, pywin32 on a non-Windows platform. If the package is actually
            # referenced, we'll get an error during the analysis phase.

            print(f'Did not find a compatible file for {pkg["name"]} {pkg["version"]}')
            continue

        dist["url"] = get_dist_url(dist, pkg, pypi_resolver)

        # Collect dependencies that apply to the current environment
        deps = evaluated_deps(pkg)

        bazel_workspace_name = f"{args.root_workspace}__{pkg['name']}-{pkg['version']}"
        output.write(format_bazel_dist(bazel_workspace_name, dist, deps))

        packages[pkg["name"]] = f"@{bazel_workspace_name}"
    output.write("\n")

    # package -> bazel label mapping
    if args.override_pkg:
        packages = {**packages, **dict(args.override_pkg)}

    output.write("packages = {\n")

    for p, label in packages.items():
        output.write(f'  "{p}": "{label}",\n')

    output.write("}\n")
    output.write("all_requirements = packages.values()\n")
    output.close()

    # requirements.bzl shim
    with open("requirements.bzl", "w") as f:
        f.write(f'load("@{args.root_workspace}//:packages.bzl", "pypi")\n')
        f.write(f"def requirement(name): return pypi(name)")


if __name__ == "__main__":
    main()
