"""Keep Pillow's private HarfBuzz separate from the Pango/WeasyPrint copy.

PyInstaller collects two different HarfBuzz builds into one bundle: Pillow
ships its own under ``PIL/__dot__dylibs``, and Pango/WeasyPrint need the
Homebrew one. Both answer to the install name ``@rpath/libharfbuzz.0.dylib``,
so whichever loads first wins for the whole process. When Pillow's wins,
``libharfbuzz-subset`` — which is only in the Homebrew build — resolves
against it and the first PDF render dies in native code.

This gives Pillow's copy a private install name, repoints Pillow's own
binaries at it, and restores the public alias to the Homebrew library.

Run after ``prepare_bundle.py`` and before final signing:

    python packaging/macos/isolate_pillow_harfbuzz.py "dist/SlowBooks Pro.app"

Safe to re-run: a bundle that has already been repaired is left alone.
"""

import argparse
import subprocess
import sys
from pathlib import Path

PUBLIC_NAME = "libharfbuzz.0.dylib"
PRIVATE_NAME = "libpillow-harfbuzz.0.dylib"


def _otool_deps(path):
    out = subprocess.run(
        ["otool", "-L", str(path)], check=True, capture_output=True, text=True
    )
    return out.stdout


def isolate(app):
    """Repair one .app bundle in place. Returns the files it modified."""
    root = app / "Contents/Frameworks"
    if not root.is_dir():
        raise SystemExit(f"Not an app bundle (no Contents/Frameworks): {app}")

    private = root / "PIL/__dot__dylibs" / PUBLIC_NAME
    renamed = private.with_name(PRIVATE_NAME)
    if not private.is_file() and not renamed.is_file():
        print(f"No Pillow HarfBuzz in {app.name}; nothing to isolate.")
        return []

    changed = []
    if private.is_file():
        private.rename(renamed)
        subprocess.run(
            ["install_name_tool", "-id", f"@rpath/{PRIVATE_NAME}", str(renamed)],
            check=True,
        )
        changed.append(renamed)
        print(f"Renamed Pillow's HarfBuzz to {PRIVATE_NAME}")

    # Repoint every Pillow binary that still links the public name.
    for path in (root / "PIL").rglob("*"):
        if (
            not path.is_file()
            or path.is_symlink()
            or path == renamed
            or path.suffix not in (".so", ".dylib")
        ):
            continue
        if f"@rpath/{PUBLIC_NAME}" in _otool_deps(path):
            subprocess.run(
                [
                    "install_name_tool",
                    "-change",
                    f"@rpath/{PUBLIC_NAME}",
                    f"@rpath/{PRIVATE_NAME}",
                    str(path),
                ],
                check=True,
            )
            changed.append(path)
            print(f"Repointed {path.relative_to(root)}")

    # PyInstaller may have made the public versioned name an alias into PIL.
    # Point it back at the independent Homebrew library Pango needs.
    public = root / PUBLIC_NAME
    if public.is_symlink() and "PIL" in str(public.readlink()):
        if not (root / "libharfbuzz.dylib").is_file():
            raise SystemExit(
                "Missing independent Pango HarfBuzz library "
                f"({root / 'libharfbuzz.dylib'}); re-run prepare_bundle.py."
            )
        public.unlink()
        public.symlink_to("libharfbuzz.dylib")
        print(f"Restored {PUBLIC_NAME} -> libharfbuzz.dylib")

    alias = root / "PIL" / PRIVATE_NAME
    if not alias.exists():
        alias.symlink_to(Path("__dot__dylibs") / PRIVATE_NAME)

    # install_name_tool invalidates the signature of everything it edits.
    # These are ad-hoc signatures for local testing; a release still gets
    # its Developer ID signature from the normal signing step afterwards.
    for path in changed:
        subprocess.run(["codesign", "--force", "--sign", "-", str(path)], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    print(f"Re-signed {len(changed)} file(s) ad-hoc and sealed {app.name}.")
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("app", type=Path, help="path to the built .app bundle")
    args = parser.parse_args(argv)
    if not args.app.exists():
        raise SystemExit(f"No such bundle: {args.app}")
    isolate(args.app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
