"""Command line entry point: ``python -m tender_tracker <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_config, now_ist


def setup_logging(data_dir: Path, verbose: bool = False) -> None:
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file_handler = RotatingFileHandler(
        data_dir / "logs" / "tracker.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.handlers[:] = [console, file_handler]
    for noisy in ("urllib3", "pdfminer", "sentence_transformers", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="tender_tracker", description="GeM tender tracker")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="fetch, score and send the digest once")
    p_run.add_argument("--no-notify", action="store_true", help="don't send anything")
    p_sched = sub.add_parser("schedule", help="run daily at the configured time (blocking)")
    p_sched.add_argument("--run-now", action="store_true", help="also run once immediately")
    p_digest = sub.add_parser("digest", help="build the digest from stored data")
    p_digest.add_argument("--send", action="store_true", help="send it (default: print only)")
    sub.add_parser("init-db", help="create database tables")
    sub.add_parser("test-notify", help="send a test alert through every configured channel")
    p_pdf = sub.add_parser("parse-pdf", help="print the fields extracted from a bid PDF")
    p_pdf.add_argument("pdf", type=Path)
    sub.add_parser("reparse", help="re-parse all stored bid PDFs with the current parser")
    sub.add_parser("rescore", help="re-score stored open tenders with the current config (no GeM requests)")
    p_cu = sub.add_parser("create-user", help="create a web app account (asks for the password)")
    p_cu.add_argument("--email", required=True)
    p_cu.add_argument("--name", default="")
    p_cu.add_argument("--admin", action="store_true", help="give the account admin rights")
    p_rp = sub.add_parser("reset-password", help="set a new password for an account and sign it out everywhere")
    p_rp.add_argument("--email", required=True)
    p_serve = sub.add_parser("serve", help="run the web app (API + frontend)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.data_dir, args.verbose)

    if args.command == "run":
        from .pipeline import run_once

        result = run_once(cfg, notify=not args.no_notify)
        print(f"Run {result.run_id}: {result.status} {result.stats or ''} {result.error or ''}")
        if result.excel_path:
            print(f"Excel: {result.excel_path}")
        return 0 if result.status == "success" else 1

    if args.command == "schedule":
        from .scheduler import start

        start(args.config, run_now=args.run_now)
        return 0

    if args.command == "parse-pdf":
        from .pdf_parser import parse_pdf

        print(json.dumps(parse_pdf(args.pdf).to_dict(), indent=2, ensure_ascii=False, default=str))
        return 0

    if args.command == "serve":
        import os

        import uvicorn

        os.environ["TT_CONFIG"] = str(args.config)
        uvicorn.run("tender_tracker.api:app", host=args.host, port=args.port)
        return 0

    from .storage import init_db, make_engine, session_factory

    engine = make_engine(cfg.storage.effective_database_url())
    init_db(engine)
    if args.command == "init-db":
        print("Database ready:", cfg.storage.effective_database_url())
        return 0

    if args.command in ("create-user", "reset-password"):
        import getpass

        from sqlalchemy import select

        from .auth import AuthError, check_password_strength, create_user, hash_password, normalise_email, revoke_sessions
        from .storage import User

        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords don't match.")
            return 1
        try:
            with session_factory(engine)() as session:
                if args.command == "create-user":
                    user = create_user(session, name=args.name, email=args.email, password=password,
                                       role="admin" if args.admin else "member")  # fmt: skip
                    print(f"Created {user.role} account {user.email}")
                else:
                    check_password_strength(password)
                    user = session.scalar(select(User).where(User.email == normalise_email(args.email)))
                    if user is None:
                        print("No account with that email.")
                        return 1
                    user.password_hash, user.status = hash_password(password), "active"
                    session.commit()
                    revoke_sessions(session, user.id)
                    print(f"Password reset for {user.email}; all its sessions were signed out.")
        except AuthError as exc:
            print(exc)
            return 1
        return 0

    if args.command == "rescore":
        from .pipeline import rescore_stored

        with session_factory(engine)() as session:
            print(rescore_stored(session, cfg))
        return 0

    if args.command == "reparse":
        from sqlalchemy import select

        from .pdf_parser import parse_pdf
        from .storage import BidDocument, save_document

        done = 0
        with session_factory(engine)() as session:
            for doc in session.scalars(select(BidDocument)).all():
                if doc.pdf_path and Path(doc.pdf_path).exists():
                    save_document(session, doc.bid, parse_pdf(Path(doc.pdf_path)), Path(doc.pdf_path))
                    session.commit()
                    done += 1
        print(f"Re-parsed {done} PDFs. Scores update on the next run.")
        return 0

    if args.command == "digest":
        from .notifier import send_digest
        from .report import build_digest, export_excel, render_text

        with session_factory(engine)() as session:
            digest = build_digest(session, cfg)
            print(render_text(digest))
            if args.send:
                path = export_excel(digest, cfg.data_dir / "exports" / f"gem_shortlist_{now_ist():%Y-%m-%d}.xlsx")
                print("Sent via:", send_digest(session, cfg, digest, path) or "nothing")
        return 0

    if args.command == "test-notify":
        from .notifier import build_channels, send_alert

        channels = build_channels(cfg)
        print("Channels:", [c.name for c in channels] or "none configured")
        send_alert(cfg, "Test message", "If you can read this, notifications work.", channels=channels)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
