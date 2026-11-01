"""Entry point: python main.py   (``--backup`` = headless automatic backup, ``--speak`` / ``--speak-hindi`` =
headless speech, used by Windows Task Scheduler and by the voice sidecars)"""
import sys

if __name__ == "__main__":
    if "--backup" in sys.argv[1:]:
        from vx7khata.autobackup import cli_main
        sys.exit(cli_main())
    if "--speak-hindi" in sys.argv[1:]:
        # Same boundary as --speak: the EXE re-enters itself as the Hindi speech sidecar.
        from vx7khata.voice.hindi_sidecar import main as hindi_main
        sys.exit(hindi_main([a for a in sys.argv[1:] if a != "--speak-hindi"]))
    if "--speak" in sys.argv[1:]:
        # The frozen EXE is its own speech sidecar: the ledger talks to this branch over pipes and stays in
        # separate process from the GPL-3 engine, so nothing of the engine links into the app.
        from vx7khata.voice.piper_sidecar import main as speak_main
        sys.exit(speak_main([a for a in sys.argv[1:] if a != "--speak"]))
    from vx7khata.app import run
    sys.exit(run())
