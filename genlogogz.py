#!/usr/bin/env python3
"""
genlogogz - Generate a new logo.mrf OTA package

Usage: python3 genlogogz.py <A.zip> <B.tar.zip or B.tar.gz>

  A.zip       : zip containing a single source file (src)
  B           : tar.gz (or tar.zip) containing logo.mrf

Output: <A_stem>_<sig>_ota.tar.gz  (written to current directory)
"""

import sys
import os
import shutil
import hashlib
import tarfile
import zipfile
import tempfile

LOGO_MRF_OFFSET = 0x00000E00          # src data starts at this offset
NEWLOGO_MRF_SIZE = 8_412_672          # pad newlogo.mrf to this size


def read_src(zip_path: str) -> tuple[bytes, str]:
    """Extract the source file bytes (and its name) from A.zip.
    Accepts any single file inside the zip."""
    with zipfile.ZipFile(zip_path) as zf:
        # Filter out directory entries
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            raise FileNotFoundError(f"No files found inside {zip_path}")
        if len(names) > 1:
            print(f"  Warning: {zip_path} contains {len(names)} files; using first: {names[0]}")
        chosen = names[0]
        return zf.read(chosen), os.path.basename(chosen)


def build_newlogo_mrf(src_data: bytes, src_name: str) -> bytes:
    """
    If src is logo.mrf: return src_data unchanged (newlogo.mrf = logo.mrf).
    Otherwise:
      - 0x00 bytes from 0 up to LOGO_MRF_OFFSET
      - src data starting at LOGO_MRF_OFFSET
      - zero-padded to NEWLOGO_MRF_SIZE bytes
    """
    if src_name == "logo.mrf":
        return src_data

    if len(src_data) > NEWLOGO_MRF_SIZE - LOGO_MRF_OFFSET:
        raise ValueError(f"{src_name} is too large to fit after offset 0x{LOGO_MRF_OFFSET:08X}")

    buf = bytearray(NEWLOGO_MRF_SIZE)                  # all zeros
    buf[LOGO_MRF_OFFSET: LOGO_MRF_OFFSET + len(src_data)] = src_data
    return bytes(buf)


def open_b_as_targz(b_path: str, tmp_dir: str) -> str:
    """
    If B is a .zip wrapping a single .tar.gz, extract it first.
    Returns path to a real .tar.gz file.
    """
    if b_path.lower().endswith(".zip"):
        with zipfile.ZipFile(b_path) as zf:
            inner_names = zf.namelist()
            tar_names = [n for n in inner_names if n.lower().endswith(".tar.gz")]
            if not tar_names:
                raise FileNotFoundError(f"No .tar.gz found inside {b_path}. Contents: {inner_names}")
            inner_name = tar_names[0]
            extracted = os.path.join(tmp_dir, os.path.basename(inner_name))
            with zf.open(inner_name) as src, open(extracted, "wb") as dst:
                shutil.copyfileobj(src, dst)
        return extracted
    else:
        # Already a .tar.gz – just copy to tmp so we can work on it freely
        dest = os.path.join(tmp_dir, os.path.basename(b_path))
        shutil.copy2(b_path, dest)
        return dest


def replace_logo_in_targz(src_tar_gz: str, newlogo_bytes: bytes, out_tar_gz: str):
    """
    Repack src_tar_gz into out_tar_gz, replacing logo.mrf with newlogo_bytes.
    """
    with tempfile.TemporaryDirectory() as extract_dir:
        # Extract everything
        with tarfile.open(src_tar_gz, "r:gz") as tf:
            tf.extractall(extract_dir)

        # Find logo.mrf anywhere in the tree
        logo_path = None
        for root, dirs, files in os.walk(extract_dir):
            for fname in files:
                if fname == "logo.mrf":
                    logo_path = os.path.join(root, fname)
                    break

        if logo_path is None:
            raise FileNotFoundError(f"logo.mrf not found inside {src_tar_gz}")

        # Overwrite logo.mrf with new content
        with open(logo_path, "wb") as f:
            f.write(newlogo_bytes)

        # Repack preserving directory structure
        with tarfile.open(out_tar_gz, "w:gz") as tf_out:
            for root, dirs, files in os.walk(extract_dir):
                for fname in files:
                    full = os.path.join(root, fname)
                    arcname = os.path.relpath(full, extract_dir)
                    tf_out.add(full, arcname=arcname)


def md5_sig(path: str) -> str:
    """Return the 2nd group of 8 hex characters of the file's MD5."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    hex_digest = h.hexdigest()          # 32 hex chars
    return hex_digest[8:16]             # characters 8-15  (2nd group of 8)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    a_zip = sys.argv[1]
    b_file = sys.argv[2]

    if not os.path.isfile(a_zip):
        sys.exit(f"ERROR: A.zip not found: {a_zip}")
    if not os.path.isfile(b_file):
        sys.exit(f"ERROR: B file not found: {b_file}")

    # Derive stem from A (the zip stem, e.g. "Screen_93_1024x600_RS070WS014-A0")
    a_stem = os.path.splitext(os.path.basename(a_zip))[0]

    print(f"[1] Reading src file from {a_zip} ...")
    src_data, src_name = read_src(a_zip)
    print(f"    src file: {src_name}  ({len(src_data)} bytes)")

    print(f"[2] Building newlogo.mrf ...")
    newlogo_bytes = build_newlogo_mrf(src_data, src_name)
    if src_name == "logo.mrf":
        print(f"    src is logo.mrf — using directly (no offset embedding)")
    else:
        print(f"    embedded at offset 0x{LOGO_MRF_OFFSET:08X}, padded to {NEWLOGO_MRF_SIZE} bytes")
    print(f"    newlogo.mrf size: {len(newlogo_bytes)} bytes")

    with tempfile.TemporaryDirectory() as tmp:
        print(f"[3] Preparing B ({b_file}) as .tar.gz ...")
        c_tar_gz = open_b_as_targz(b_file, tmp)
        print(f"    Working tar.gz: {c_tar_gz}")

        # Rename stem to A's stem
        out_tar_gz_base = f"{a_stem}_ota.tar.gz"
        out_tar_gz_tmp  = os.path.join(tmp, out_tar_gz_base)

        print(f"[4] Repacking tar.gz with new logo.mrf ...")
        replace_logo_in_targz(c_tar_gz, newlogo_bytes, out_tar_gz_tmp)
        print(f"    Repacked: {out_tar_gz_tmp}  ({os.path.getsize(out_tar_gz_tmp)} bytes)")

        print(f"[5] Calculating MD5 signature ...")
        sig = md5_sig(out_tar_gz_tmp)
        print(f"    MD5 2nd-group-8: {sig}")

        # Final filename
        final_name = f"{a_stem}_{sig}_ota.tar.gz"
        final_path = os.path.join(os.getcwd(), final_name)
        shutil.copy2(out_tar_gz_tmp, final_path)

    print(f"[6] Output: {final_name}  ({os.path.getsize(final_path)} bytes)")
    print("Done.")


if __name__ == "__main__":
    main()
