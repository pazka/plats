"""Command line: `python -m app.cli {migrate,import-wikidata,set-admin}`."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path

ENV_KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def cmd_migrate(_args) -> int:
    from . import db
    from .config import load_settings

    settings = load_settings()
    version = db.migrate(settings.db_path)
    print(json.dumps({"event": "migrated", "schema": version, "db": str(settings.db_path)}))
    return 0


def cmd_import(args) -> int:
    from . import db, importer
    from .config import load_settings
    from .web import setup_logging

    setup_logging()
    settings = load_settings()
    db.migrate(settings.db_path)
    try:
        summary = importer.run_logged(settings.db_path, settings.images_dir, enrich=not args.no_enrich,
                                      images=args.images, actor="cli")
    except Exception as exc:  # noqa: BLE001 - details are in the JSON log line above
        print(json.dumps({"event": "import_failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


# ------------------------------------------------------------------ set-admin

def _tty_out():
    try:
        return open("/dev/tty", "w")
    except OSError:
        return sys.stderr


def merge_env(path: Path, values: dict[str, str]) -> None:
    """Replace/append KEY='value' lines, keep everything else, write atomically with mode 0600."""
    lines = path.read_text().splitlines() if path.exists() else []
    done = set()
    out = []
    for line in lines:
        m = ENV_KEY_RE.match(line)
        if m and m.group(1) in values:
            if m.group(1) not in done:
                out.append(f"{m.group(1)}='{values[m.group(1)]}'")
                done.add(m.group(1))
            continue
        out.append(line)
    for key, value in values.items():
        if key not in done:
            out.append(f"{key}='{value}'")
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(out) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def cmd_set_admin(args) -> int:
    import pyotp
    import qrcode

    from .auth import hash_password

    tty = _tty_out()
    env_path = Path(args.env_file)
    print("== plats.hosh.it : identifiants administrateur ==", file=tty)
    print(f"Fichier d'environnement : {env_path}", file=tty)
    while True:
        pw = getpass.getpass("Nouveau mot de passe admin (12 caractères min.) : ")
        if len(pw) < 12:
            print("Trop court.", file=tty)
            continue
        if pw != getpass.getpass("Confirmez : "):
            print("Les deux saisies diffèrent.", file=tty)
            continue
        break
    pw_hash = hash_password(pw)
    del pw

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name="admin", issuer_name="plats.hosh.it")
    qr = qrcode.QRCode(border=1)
    qr.add_data(uri)
    qr.make(fit=True)
    print("\nScannez ce QR code avec votre application TOTP (Aegis, 2FAS, Google Authenticator…) :\n", file=tty)
    qr.print_ascii(out=tty, invert=True)
    print(f"\nSaisie manuelle possible — clé : {secret}\n", file=tty)
    tty.flush()
    for _ in range(3):
        code = input("Code à 6 chiffres affiché par l'application (vérification) : ").strip().replace(" ", "")
        if totp.verify(code, valid_window=1):
            break
        print("Code incorrect, réessayez.", file=tty)
    else:
        print("Échec de vérification : rien n'a été écrit.", file=tty)
        return 1

    merge_env(env_path, {"PLATS_ADMIN_PASSWORD_HASH": pw_hash, "PLATS_ADMIN_TOTP_SECRET": secret})
    print(f"OK : {env_path} mis à jour (mode 600). Redémarrez le conteneur pour appliquer.", file=tty)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply database migrations").set_defaults(func=cmd_migrate)
    p = sub.add_parser("import-wikidata", help="import/update dishes from Wikidata (idempotent)")
    p.add_argument("--no-enrich", action="store_true", help="don't fetch frwiki summaries for short descriptions")
    p.add_argument("--images", action="store_true", help="download Commons thumbnails (with licence) locally")
    p.set_defaults(func=cmd_import)
    p = sub.add_parser("set-admin", help="interactive: admin password + TOTP into an env file")
    p.add_argument("--env-file", required=True)
    p.set_defaults(func=cmd_set_admin)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
