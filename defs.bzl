def _peotry_import_impl(repo_ctx):
    repo_ctx.report_progress("Processing %s" % repo_ctx.attr.lock_file)

    cmd = [
        "python3",
        repo_ctx.path(Label("//:rules_python_poetry.py")),
        repo_ctx.path(repo_ctx.attr.lock_file),
        "--root-workspace",
        repo_ctx.attr.name,
    ]

    for k, v in repo_ctx.attr.override_pkg.items():
        cmd.extend(["--override-pkg", "{}={}".format(k, v)])

    result = repo_ctx.execute(cmd)

    if repo_ctx.attr.verbose:
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)

    if result.return_code:
        fail("poetry_import failed:\n stdout: %s\n stderr: %s" % (result.stdout, result.stderr))

    repo_ctx.file("BUILD", "")

def _pip_install_sdist(repo_ctx):
    sdist_filename = repo_ctx.attr.url[repo_ctx.attr.url.rindex("/") + 1:]
    repo_ctx.download(
        url=repo_ctx.attr.url,
        output=sdist_filename,
        sha256=repo_ctx.attr.sha256,
    )

    # FIXME: reproducibility: PYTHONHASHSEED, SOURCE_DATE_EPOCH, what else?
    repo_ctx.report_progress("Installing {} through pip".format(sdist_filename))
    result = repo_ctx.execute(
        [
            "python3",
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--no-cache-dir",
            "--isolated",
            "--force-reinstall",
            "--upgrade",
            "--target=.",
            repo_ctx.path(sdist_filename),

        ],
        environment={
            'PYTHONPATH': str(repo_ctx.path(repo_ctx.attr._pip_whl))
        },
    )
    repo_ctx.delete(sdist_filename)

    if repo_ctx.attr.verbose:
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)

    if result.return_code != 0:
        fail("Invoking pip failed with status code {sc}\nstdout: {stdout}\nstderr: {stderr}".format(
            sc=result.return_code,
            stdout=result.stdout,
            stderr=result.stderr,
        ))

    if repo_ctx.attr.build_file_content:
        repo_ctx.file(
            'BUILD.bazel',
            repo_ctx.attr.build_file_content,
            executable=False
        )


poetry_import = repository_rule(
    attrs = {
        "lock_file": attr.label(
            mandatory = True,
            allow_single_file = True,

            doc = "poetry.lock file"
        ),
        "override_pkg": attr.string_dict(
            doc="Mapping of package names to Bazel labels to override in the dependency graph"
        ),
        "verbose": attr.bool(),
    },
    implementation = _peotry_import_impl
)

pip_install_sdist = repository_rule(
    attrs = {
        "url": attr.string(mandatory=True),
        "sha256": attr.string(),
        "build_file_content": attr.string(),
        "verbose": attr.bool(),
        "_pip_whl": attr.label(
            allow_single_file=True,
            default="//:third_party/pip-21.0.1-py3-none-any.whl"
        ),
    },
    implementation = _pip_install_sdist
)
